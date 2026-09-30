"""A gate script run as a program against a scratch repository: real workers, real testinfra,
no network."""

import os
import signal
import subprocess
import sys
import time

from conftest import write

HELPERS = """
from preflight import Outcome, Probe, check, fail, ok, outcome


@check(key="name")
def marker(probe: Probe, name: str) -> Outcome:
    present = (probe.root / name).exists()
    return outcome(ok() if present else fail(do=f"Create {name}."))
"""

GATE = """
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from preflight import Arg, Depends, Gate, Guards, Outcome, Probe, check, fail, ok, outcome
from preflight.catalog import files

import helpers

Env = Annotated[Literal["dev", "prod"], Arg()]
LOG = Path(__file__).resolve().parent.parent / "cleanup.log"


@check
def env_file(probe: Probe, env: str) -> Outcome:
    path = probe.root / f"{env}.env"
    return outcome(ok() if path.exists() else fail(do=f"Create {env}.env."))


def logged(env: Env) -> Iterator[str]:
    yield env
    LOG.write_text(env)


Logged = Annotated[str, Depends(logged)]

shared = Guards()


@shared.guard("readme present")
def readme():
    return [files.present(paths=["README.md"])]


gate = Gate("ready")
gate.include(shared)


@gate.guard("environment ready")
def environment(env: Env, seen: Logged):
    return [env_file(env=env), helpers.marker(name="MARKER")]


if __name__ == "__main__":
    gate.run()
"""


SLOW = """
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from preflight import Depends, Gate, Outcome, Probe, check, ok, outcome

LOG = Path(__file__).resolve().parent.parent / "cleanup.log"


@check
def sleeps(probe: Probe) -> Outcome:
    (probe.root / "worker.pid.tmp").write_text(str(os.getpid()))
    (probe.root / "worker.pid.tmp").rename(probe.root / "worker.pid")
    time.sleep(20)
    return outcome(ok())


def logged() -> Iterator[str]:
    yield "held"
    LOG.write_text("cleaned")


gate = Gate("slow")


@gate.guard("waits")
def waits(held: Annotated[str, Depends(logged)]):
    return [sleeps()]


if __name__ == "__main__":
    gate.run()
"""


def setup(repo):
    write(repo, "gate/helpers.py", HELPERS)
    return write(repo, "gate/ready.py", GATE)


def run(gate_file, *args, cwd, env=None):
    return subprocess.run(
        [sys.executable, str(gate_file), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_first_failing_guard_stops_the_run(repo):
    gate = setup(repo)
    result = run(gate, "dev", cwd=repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "  ✗ readme present\n" in result.stdout
    assert "Create README.md." in result.stdout
    assert "Not run: environment ready" in result.stdout
    assert not (repo / "cleanup.log").exists()


def test_a_check_in_the_gate_file_and_one_in_a_helper_run_in_workers(repo):
    gate = setup(repo)
    write(repo, "README.md", "x\n")
    result = run(gate, "dev", cwd=repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Create dev.env." in result.stdout
    assert "Create MARKER." in result.stdout
    assert (repo / "cleanup.log").read_text() == "dev"


def test_a_gate_runs_from_any_directory(repo, tmp_path_factory):
    gate = setup(repo)
    for name in ("README.md", "prod.env", "MARKER"):
        write(repo, name, "x\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    result = run(gate, "prod", cwd=elsewhere)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith("Every guard passed.\n")
    assert "… readme present" in result.stderr


def test_a_bad_argument_prints_usage_and_exits_2(repo):
    result = run(setup(repo), "qa", cwd=repo)
    assert result.returncode == 2
    assert "invalid choice: 'qa'" in result.stderr


def test_validate_observes_nothing(repo):
    result = run(setup(repo), "dev", "--validate", cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated every guard; nothing was observed." in result.stdout
    assert (repo / "cleanup.log").read_text() == "dev"


def test_no_bytecode_is_left_in_the_repository(repo):
    gate = setup(repo)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    result = run(gate, "dev", cwd=repo, env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (repo / "gate" / "__pycache__").exists()
    assert list(repo.rglob("__pycache__")) == []


def _wait_for(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_sigterm_kills_workers_cleans_up_and_exits_130(repo):
    gate = write(repo, "gate/slow.py", SLOW)
    process = subprocess.Popen(
        [sys.executable, str(gate)],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stderr.readline() == "… waits\n"
        pid_file = repo / "worker.pid"
        _wait_for(pid_file.exists, 5)
        worker = int(pid_file.read_text())
        process.send_signal(signal.SIGTERM)
        stdout, _ = process.communicate(timeout=5)
    finally:
        process.kill()
    assert process.returncode == 130
    assert "interrupted during guard 'waits'" in stdout
    assert (repo / "cleanup.log").read_text() == "cleaned"
    _wait_for(lambda: _gone(worker), 3)


PROGRAM = """
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from preflight import Arg, Depends, Gate, Guards, Outcome, Probe, check, fail, ok, outcome
from preflight.catalog import files

Env = Annotated[Literal["dev", "prod"], Arg()]
ROOT = Path(__file__).resolve().parent.parent


def session(env: Env) -> Iterator[str]:
    yield env
    (ROOT / "cleanup.log").write_text(env)


gate = Gate("program")


@gate.guard("readme present")
def readme(held: Annotated[str, Depends(session)]):
    return [files.present(paths=["README.md"])]


after = Guards()


@after.guard("marker written")
def marker():
    return [files.present(paths=["MARKER"])]


if __name__ == "__main__":
    with gate.checked(sys.argv[1:]) as run:
        (ROOT / "MARKER").write_text(run[session])
        run.verify(after)
"""


def test_a_program_acts_after_its_guards_and_verifies(repo):
    program = write(repo, "gate/program.py", PROGRAM)
    write(repo, "README.md", "x\n")
    result = run(program, "dev", cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "MARKER").read_text() == "dev"
    assert (repo / "cleanup.log").read_text() == "dev"
    assert result.stdout.count("Every guard passed.") == 2


def test_a_program_never_acts_when_a_guard_stops(repo):
    program = write(repo, "gate/program.py", PROGRAM)
    result = run(program, "dev", cwd=repo)
    assert result.returncode == 1
    assert not (repo / "MARKER").exists()
    assert "Create README.md." in result.stdout


LIBRARY_GATE = """
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from preflight import Gate

import helpers2

gate = Gate("library")


@gate.guard("marker present")
def marker():
    return [helpers2.marker(name="MARKER")]


if __name__ == "__main__":
    gate.run()
"""


def test_a_check_module_imported_from_a_directory_the_gate_added_runs(repo):
    write(repo, "lib/helpers2.py", HELPERS)
    write(repo, "MARKER", "x\n")
    gate = write(repo, "gate/library.py", LIBRARY_GATE)
    result = run(gate, cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
