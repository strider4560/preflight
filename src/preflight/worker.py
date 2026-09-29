"""One check instance in its own process.

`python -m preflight.worker` reads a job as JSON on stdin and writes the outcome as JSON on
stdout. Nothing else leaves the process: the launcher discards stderr, and kills the whole
process group when the instance runs out of time."""

from __future__ import annotations

import datetime
import importlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from preflight.check import REGISTRY
from preflight.context import Context
from preflight.gate import register_consumer
from preflight.identity import Identity
from preflight.outcome import Outcome, error, outcome
from preflight.resolvers import LazySsm, ResolveError, apply_ssm_value


@dataclass(frozen=True)
class Job:
    node_id: str
    check_id: str
    module: str
    consumer_dir: str | None
    root: str
    environment: str | None
    data: dict[str, Any]
    identity: dict[str, Any] | None
    identities: dict[str, dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Job:
        return cls(**json.loads(text))


def encode_data(value: Any) -> Any:
    if isinstance(value, datetime.date | datetime.time):  # datetime is a date
        return value.isoformat()
    if isinstance(value, LazySsm):
        return value.to_dict()
    if isinstance(value, dict):
        return {key: encode_data(element) for key, element in value.items()}
    if isinstance(value, list):
        return [encode_data(element) for element in value]
    return value


def decode_data(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__lazy_ssm__"}:
            return LazySsm.from_dict(value)
        return {key: decode_data(element) for key, element in value.items()}
    if isinstance(value, list):
        return [decode_data(element) for element in value]
    return value


class _Unresolved(Exception):
    pass


def _resolve_lazy(value: Any, ctx: Context) -> Any:
    if isinstance(value, LazySsm):
        identity = ctx.identities.get(value.identity)
        if identity is None:
            raise _Unresolved(
                f"SSM parameter {value.name} needs identity {value.identity}, "
                "which is not filled in."
            )
        raw = ctx.ssm_lookup(value.name, identity=identity)
        if raw is None:
            raise _Unresolved(f"SSM parameter {value.name} is missing.")
        try:
            return apply_ssm_value(raw, value)
        except ResolveError as exc:
            raise _Unresolved(f"SSM parameter {value.name}: {exc}.") from None
    if isinstance(value, dict):
        return {key: _resolve_lazy(element, ctx) for key, element in value.items()}
    if isinstance(value, list):
        return [_resolve_lazy(element, ctx) for element in value]
    return value


def execute(job: Job, *, context_factory: Callable[..., Context] = Context) -> Outcome:
    """Runs inside the worker; every failure becomes an error outcome carrying only a type."""
    try:
        if job.consumer_dir:
            register_consumer(Path(job.consumer_dir))
        importlib.import_module(job.module)
        check = REGISTRY[job.check_id]
        identities = {
            alias: Identity.model_validate(spec) for alias, spec in job.identities.items()
        }
        identity = Identity.model_validate(job.identity) if job.identity else None
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=f"Preflight could not load {job.check_id} ({type(exc).__name__}).",
                error_type=type(exc).__name__,
            )
        )
    ctx = context_factory(
        root=Path(job.root), environment=job.environment, identity=identity, identities=identities
    )
    try:
        data = _resolve_lazy(decode_data(job.data), ctx)
    except _Unresolved as exc:
        return outcome(error(do=str(exc), error_type="Unresolved"))
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=(
                    f"Could not read an SSM value for {job.check_id}; "
                    "check the session, then rerun."
                ),
                error_type=type(exc).__name__,
            )
        )
    try:
        section = check.section.model_validate(data)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        return outcome(
            error(
                do=(
                    f"After reading SSM, field(s) {', '.join(fields)} have the wrong shape; "
                    "check the parameters they reference."
                ),
                error_type="ValidationError",
            )
        )
    try:
        result = check.observe(ctx, section)
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=(
                    f"Could not observe {job.check_id}; "
                    "check credentials, network and tools, then rerun."
                ),
                error_type=type(exc).__name__,
            )
        )
    if not isinstance(result, Outcome):
        return outcome(
            error(
                do=(
                    f"{job.check_id} returned {type(result).__name__}, not an Outcome; "
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
    try:
        payload = job.to_json()
    except (TypeError, ValueError):
        return outcome(
            error(
                do="The contract data for this check cannot be passed to a worker.",
                error_type="TypeError",
            )
        )
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
                        f"Timed out after {timeout:g} s; rerun, or raise this section's timeout "
                        "if the probe is legitimately slow."
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
                    f"The worker for {job.check_id} exited with status {process.returncode}; "
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
                    f"The worker for {job.check_id} produced unreadable output; "
                    "a check may be printing to stdout."
                ),
                error_type="WorkerOutput",
            )
        )


def main() -> int:
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
