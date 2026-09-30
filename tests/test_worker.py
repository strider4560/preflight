import json
import os
import sys
import time

import pytest
from conftest import write

from preflight import worker
from preflight.outcome import Status
from preflight.worker import Job, execute, kill_all, launch, reset_stop

CHECKS = """
import os
import subprocess
import time
from pathlib import Path

from preflight.check import check
from preflight.outcome import fail, ok, outcome


@check
def sample(probe, mode: str = "ok", servers: dict[str, list[str]] = {}):
    if mode == "fail":
        return outcome(fail(do="Fix it."))
    if mode == "raise":
        raise RuntimeError("password=hunter2")
    if mode == "wrong":
        return "not an outcome"
    if mode == "sleep":
        child = subprocess.Popen(["sleep", "30"])
        Path(probe.root, "child.pid").write_text(str(child.pid))
        time.sleep(30)
    if mode == "escape":
        subprocess.Popen(["setsid", "sleep", "30"])
    if mode == "escape_sleep":
        subprocess.Popen(["setsid", "sleep", "30"])
        time.sleep(30)
    if mode == "print":
        os.write(1, b"junk")
    if mode == "exit":
        os._exit(3)
    return outcome(ok(observed=servers))
"""

GATE = """
from pathlib import Path

from preflight.check import check
from preflight.outcome import ok, outcome


@check
def where(probe):
    return outcome(ok(observed=str(probe.root)))


if __name__ == "__main__":
    Path(__file__).with_name("ran-as-main").write_text("yes")
"""


def job(repo, **arguments):
    write(repo, "gate/workerchecks.py", CHECKS)
    return Job(
        label="workerchecks.sample",
        module="workerchecks",
        file=None,
        name="sample",
        gate_dir=str(repo / "gate"),
        root=str(repo),
        arguments=json.dumps(arguments),
    )


@pytest.fixture(autouse=True)
def _clean_imports():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in [n for n in sys.modules if n == "workerchecks" or n.startswith("preflight_gate_")]:
        del sys.modules[name]
    reset_stop()


def test_execute_returns_the_checks_outcome(repo):
    assert execute(job(repo, mode="fail")).status is Status.FAIL


def test_an_exception_becomes_an_error_carrying_only_its_type(repo):
    result = execute(job(repo, mode="raise"))
    assert result.items[0].error_type == "RuntimeError"
    assert "hunter2" not in json.dumps(result.to_dict())


def test_a_non_outcome_is_an_error(repo):
    assert execute(job(repo, mode="wrong")).items[0].error_type == "TypeError"


def test_arguments_that_no_longer_validate_are_an_error(repo):
    item = execute(job(repo, mode=3)).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "ValidationError")


def test_launch_runs_the_check_in_a_worker_process(repo):
    result = launch(
        job(repo, mode="ok", servers={"app": ["ns-1"]}), env=dict(os.environ), timeout=60
    )
    assert result.items[0].observed == {"app": ["ns-1"]}


def test_a_check_in_a_gate_file_loads_without_running_the_gate(repo):
    gate = write(repo, "gate/bootstrap.py", GATE)
    loaded = Job(
        label="bootstrap.where",
        module="__main__",
        file=str(gate),
        name="where",
        gate_dir=str(gate.parent),
        root=str(repo),
        arguments="{}",
    )
    result = launch(loaded, env=dict(os.environ), timeout=60)
    assert result.items[0].observed == str(repo)
    assert not (repo / "gate" / "ran-as-main").exists()


def test_a_timeout_kills_the_whole_process_group(repo):
    result = launch(job(repo, mode="sleep"), env=dict(os.environ), timeout=5)
    assert result.items[0].error_type == "Timeout"
    pid = int((repo / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the worker's child survived the timeout")


def test_stray_output_does_not_corrupt_the_result_and_crashes_are_errors(repo):
    env = dict(os.environ)
    assert launch(job(repo, mode="print"), env=env, timeout=60).status is Status.OK
    assert launch(job(repo, mode="exit"), env=env, timeout=60).items[0].error_type == (
        "WorkerCrashed"
    )


def test_a_gate_module_shadowing_a_dependency_does_not_break_the_worker(repo):
    write(repo, "gate/pydantic.py", 'raise RuntimeError("shadow")\n')
    result = launch(job(repo, mode="fail"), env=dict(os.environ), timeout=60)
    assert result.status is Status.FAIL


def test_a_detached_descendant_does_not_hold_the_result_hostage(repo):
    started = time.monotonic()
    result = launch(job(repo, mode="escape"), env=dict(os.environ), timeout=60)
    assert result.status is Status.OK
    assert time.monotonic() - started < 15


def test_a_timeout_with_a_detached_descendant_still_returns_promptly(repo):
    started = time.monotonic()
    result = launch(job(repo, mode="escape_sleep"), env=dict(os.environ), timeout=3)
    assert result.items[0].error_type == "Timeout"
    assert time.monotonic() - started < 10


def test_launch_refuses_after_kill_all_until_reset(repo, monkeypatch):
    kill_all()
    env = dict(os.environ)
    assert launch(job(repo, mode="fail"), env=env, timeout=60).items[0].error_type == (
        "Interrupted"
    )
    reset_stop()
    assert launch(job(repo, mode="fail"), env=env, timeout=60).status is Status.FAIL


def test_the_worker_writes_no_bytecode_into_the_gate_directory(repo):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    assert launch(job(repo), env=env, timeout=60).status is Status.OK
    assert list((repo / "gate").rglob("__pycache__")) == []


def test_worker_module_has_no_graph_leftovers():
    assert not hasattr(worker, "encode_data")
