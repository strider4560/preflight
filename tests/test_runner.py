import importlib
import sys
import time

import pytest
from conftest import write
from fakes import IDENTITY

from preflight import runner
from preflight.outcome import Status
from preflight.runner import StandInExecutor, WorkerExecutor, probe_now, using

CHECKS = """
import os
import time

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import fail, ok, outcome


@check(key="name")
def sample(probe, name: str, mode: str = "ok"):
    if mode == "sleep":
        time.sleep(2)
    return outcome(fail(do=f"Fix {name}.") if mode == "fail" else ok(observed=name))


@check
def environment(probe, identity: Identity):
    keys = ("AWS_PROFILE", "AWS_REGION", "AWS_ACCESS_KEY_ID")
    return outcome(ok(observed={k: os.environ.get(k) for k in keys}))
"""


@pytest.fixture
def checks(repo, monkeypatch):
    write(repo, "gate/runnerchecks.py", CHECKS)
    monkeypatch.syspath_prepend(str(repo / "gate"))
    module = importlib.import_module("runnerchecks")
    yield module
    sys.modules.pop("runnerchecks", None)


def executor(repo, jobs=4):
    return WorkerExecutor(root=repo, gate_dir=repo / "gate", jobs=jobs)


def test_checks_run_in_parallel_workers_and_keep_their_order(repo, checks):
    bound = [checks.sample(name=n, mode="sleep") for n in ("a", "b", "c")]
    started = time.monotonic()
    results = executor(repo).run(bound)
    assert time.monotonic() - started < 5
    assert [r.items[0].observed for r in results] == ["a", "b", "c"]


def test_the_job_names_the_module_and_the_arguments(repo, checks):
    job = executor(repo).job(checks.sample(name="x"))
    assert (job.label, job.module, job.name) == ("runnerchecks.sample(x)", "runnerchecks", "sample")
    assert job.gate_dir == str(repo / "gate") and job.root == str(repo)
    assert job.arguments == '{"name":"x","mode":"ok"}'


def test_an_identity_argument_sets_the_workers_profile_and_region(repo, checks, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASTRAY")
    (result,) = executor(repo).run([checks.environment(identity=IDENTITY)])
    assert result.items[0].observed == {
        "AWS_PROFILE": "sandbox",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": None,
    }


def test_stand_ins_observe_nothing(repo, checks, monkeypatch):
    def no_launch(*args, **kwargs):
        raise AssertionError("a worker was launched")

    monkeypatch.setattr(runner, "launch", no_launch)
    (result,) = StandInExecutor().run([checks.sample(name="x", mode="fail")])
    assert result.status is Status.OK
    assert result.items[0].observed == "not observed (--validate)"


def test_probe_now_runs_only_inside_a_run(repo, checks):
    with pytest.raises(RuntimeError, match="only while a gate runs"):
        probe_now(checks.sample(name="x"))
    with using(StandInExecutor()):
        assert probe_now(checks.sample(name="x")).status is Status.OK
    with using(executor(repo)):
        assert probe_now(checks.sample(name="x", mode="fail")).status is Status.FAIL
