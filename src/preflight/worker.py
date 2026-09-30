"""One bound check in its own process.

`python -P -m preflight.worker` reads a job as JSON on stdin and writes the outcome as JSON to a
private copy of the original stdout, taken before the check runs; the check's own prints go to
stderr. Nothing else leaves the process: the launcher discards stderr, and kills the whole
process group when the check runs out of time."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

from preflight.check import Check
from preflight.identity import Identity
from preflight.outcome import Outcome, error, outcome
from preflight.probe import Probe


@dataclass(frozen=True)
class Job:
    label: str
    module: str
    file: str | None
    name: str
    gate_dir: str | None
    root: str
    arguments: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Job:
        return cls(**json.loads(text))


def load_module(job: Job) -> ModuleType:
    """The check's module. A gate file loads by path under a name of its own, so its
    `if __name__ == "__main__": gate.run()` does not fire. The gate's directory is appended to
    the path, after preflight's own imports, so a gate's modules cannot shadow them. So is the
    directory of the check's own file, for a module the gate imported from a directory it put
    on its own path."""
    if job.gate_dir and job.gate_dir not in sys.path:
        sys.path.append(job.gate_dir)
    if job.module != "__main__":
        if job.file:
            home = str(Path(job.file).parent)
            if home not in sys.path:
                sys.path.append(home)
        return importlib.import_module(job.module)
    if not job.file:
        raise ImportError("the gate file is unknown")
    name = f"preflight_gate_{Path(job.file).stem}"
    spec = importlib.util.spec_from_file_location(name, job.file)
    if spec is None or spec.loader is None:
        raise ImportError(job.file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def execute(job: Job, *, probe_factory: Callable[..., Probe] = Probe) -> Outcome:
    """Runs inside the worker; every failure becomes an error outcome carrying only a type."""
    try:
        check = getattr(load_module(job), job.name)
        if not isinstance(check, Check):
            raise TypeError(f"{job.name} is not a check")
        arguments = check.arguments.model_validate_json(job.arguments)
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=f"Preflight could not load {job.label} ({type(exc).__name__}).",
                error_type=type(exc).__name__,
            )
        )
    values = {name: getattr(arguments, name) for name in type(arguments).model_fields}
    identity = next((v for v in values.values() if isinstance(v, Identity)), None)
    probe = probe_factory(root=Path(job.root), identity=identity)
    try:
        result = check.observe(probe, **values)
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=(
                    f"Could not observe {job.label}; "
                    "check credentials, network and tools, then rerun."
                ),
                error_type=type(exc).__name__,
            )
        )
    if not isinstance(result, Outcome):
        return outcome(
            error(
                do=(
                    f"{job.label} returned {type(result).__name__}, not an Outcome; "
                    "this is a bug in the check."
                ),
                error_type="TypeError",
            )
        )
    return result


_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()
_STOPPING = False


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def kill_all() -> None:
    """Stops every live worker and makes later launches refuse until `reset_stop`."""
    global _STOPPING
    with _LIVE_LOCK:
        _STOPPING = True
        for process in list(_LIVE):
            _kill_group(process)


def reset_stop() -> None:
    global _STOPPING
    with _LIVE_LOCK:
        _STOPPING = False


def _interrupted() -> Outcome:
    return outcome(error(do="Preflight was interrupted.", error_type="Interrupted"))


def _reap(process: subprocess.Popen) -> None:
    """Reaps a killed worker without waiting on a pipe a detached descendant may hold open."""
    if process.stdout:
        process.stdout.close()
    if process.stdin:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_group(process)


def launch(
    job: Job, *, env: Mapping[str, str], timeout: float, python: str = sys.executable
) -> Outcome:
    payload = job.to_json()
    with _LIVE_LOCK:
        if _STOPPING:
            return _interrupted()
    process = subprocess.Popen(
        [python, "-P", "-m", "preflight.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=job.root,
        env=dict(env),
        start_new_session=True,
        text=True,
    )
    with _LIVE_LOCK:
        stopping = _STOPPING
        if not stopping:
            _LIVE.add(process)
    if stopping:
        _kill_group(process)
        _reap(process)
        return _interrupted()
    try:
        try:
            stdout, _ = process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            _reap(process)
            return outcome(
                error(
                    do=(
                        f"Timed out after {timeout:g} s; rerun, or pass a larger timeout= to "
                        "the check if the probe is legitimately slow."
                    ),
                    error_type="Timeout",
                )
            )
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(process)
    if process.returncode != 0:
        return outcome(
            error(
                do=(
                    f"The worker for {job.label} exited with status {process.returncode}; "
                    "rerun, and report it if it persists."
                ),
                error_type="WorkerCrashed",
            )
        )
    try:
        return Outcome.from_dict(json.loads(stdout))
    except (ValueError, KeyError, TypeError):
        return outcome(
            error(
                do=(
                    f"The worker for {job.label} produced unreadable output; "
                    "a check may be printing to stdout."
                ),
                error_type="WorkerOutput",
            )
        )


def main() -> int:
    sys.dont_write_bytecode = True  # checks are imported from the consumer's repository
    job = Job.from_json(sys.stdin.read())
    # The result pipe moves to a private, non-inheritable descriptor, so neither the check's
    # prints nor a descendant it leaves behind can touch or hold open the result.
    result_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    result = execute(job)
    with os.fdopen(result_fd, "w") as sink:
        sink.write(json.dumps(result.to_dict(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
