"""A gate script run as a program against a scratch repository: real workers, real testinfra,
no network."""

import subprocess
import sys

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


def setup(repo):
    write(repo, "gate/helpers.py", HELPERS)
    return write(repo, "gate/ready.py", GATE)


def run(gate_file, *args, cwd):
    return subprocess.run(
        [sys.executable, str(gate_file), *args],
        cwd=cwd,
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
    run(gate, "dev", cwd=repo)
    assert list(repo.rglob("__pycache__")) == []
