import datetime
import json
import os
import time

import pytest
from conftest import write
from fakes import IDENTITY, FakeAnsibleHost, make_ctx

from preflight import worker
from preflight.outcome import Status
from preflight.resolvers import LazySsm
from preflight.worker import Job, decode_data, encode_data, execute, kill_all, launch, reset_stop

CHECKS = """
import os
import subprocess
import time
from pathlib import Path

from preflight import Section, check, fail, ok, outcome


class Plain(Section):
    mode: str = "ok"
    servers: dict[str, list[str]] = {}
    when: str = ""


@check("worker.sample", section=Plain)
def sample(ctx, s):
    if s.mode == "fail":
        return outcome(fail(do="Fix it."))
    if s.mode == "raise":
        raise RuntimeError("password=hunter2")
    if s.mode == "wrong":
        return "not an outcome"
    if s.mode == "sleep":
        child = subprocess.Popen(["sleep", "30"])
        Path(ctx.root, "child.pid").write_text(str(child.pid))
        time.sleep(30)
    if s.mode == "escape":
        subprocess.Popen(["setsid", "sleep", "30"])
    if s.mode == "escape_sleep":
        subprocess.Popen(["setsid", "sleep", "30"])
        time.sleep(30)
    if s.mode == "when":
        return outcome(ok(observed={"when": s.when}))
    if s.mode == "print":
        os.write(1, b"junk")
    if s.mode == "exit":
        os._exit(3)
    return outcome(ok(observed=s.servers))
"""


def job(repo, data):
    write(repo, "preflight/checks/workerchecks.py", CHECKS)
    return Job(
        node_id="worker.sample[x]",
        check_id="worker.sample",
        module="consumer.checks.workerchecks",
        consumer_dir=str(repo / "preflight"),
        root=str(repo),
        environment="dev",
        data=encode_data(data),
        identity=None,
        identities={"admin": IDENTITY.model_dump()},
    )


def ssm_returning(text):
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"msg": text}})
    return lambda **kw: make_ctx(kw["root"], ansible=ansible)


def test_lazy_values_survive_encoding():
    data = {"a": [LazySsm("/x", "admin")], "b": 1}
    assert decode_data(json.loads(json.dumps(encode_data(data)))) == data


def test_execute_returns_the_checks_outcome(repo):
    assert execute(job(repo, {"mode": "fail"})).status is Status.FAIL


def test_an_exception_becomes_an_error_carrying_only_its_type(repo):
    result = execute(job(repo, {"mode": "raise"}))
    assert result.items[0].error_type == "RuntimeError"
    assert "hunter2" not in json.dumps(result.to_dict())


def test_a_non_outcome_is_an_error(repo):
    assert execute(job(repo, {"mode": "wrong"})).items[0].error_type == "TypeError"


def test_lazy_ssm_values_are_resolved_before_validation(repo):
    data = {"servers": LazySsm("/platform/dns/name_servers", "admin", "json")}
    result = execute(job(repo, data), context_factory=ssm_returning('{"app": ["ns-1"]}'))
    assert result.items[0].observed == {"app": ["ns-1"]}


def test_a_lazy_value_of_the_wrong_shape_names_the_field(repo):
    data = {"servers": LazySsm("/platform/dns/name_servers", "admin", "json")}
    item = execute(job(repo, data), context_factory=ssm_returning('["a"]')).items[0]
    assert item.error_type == "ValidationError"
    assert "servers" in item.next_step.do


def test_launch_runs_the_check_in_a_worker_process(repo):
    assert (
        launch(job(repo, {"mode": "fail"}), env=dict(os.environ), timeout=60).status is Status.FAIL
    )


def test_a_timeout_kills_the_whole_process_group(repo):
    result = launch(job(repo, {"mode": "sleep"}), env=dict(os.environ), timeout=5)
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


@pytest.fixture(autouse=True)
def _reset_stop():
    yield
    reset_stop()


def test_stray_output_does_not_corrupt_the_result_and_crashes_are_errors(repo):
    env = dict(os.environ)
    assert launch(job(repo, {"mode": "print"}), env=env, timeout=60).status is Status.OK
    assert (
        launch(job(repo, {"mode": "exit"}), env=env, timeout=60).items[0].error_type
        == "WorkerCrashed"
    )


def test_a_consumer_module_shadowing_a_dependency_does_not_break_the_worker(repo):
    write(repo, "pydantic.py", 'raise RuntimeError("shadow")\n')
    result = launch(job(repo, {"mode": "fail"}), env=dict(os.environ), timeout=60)
    assert result.status is Status.FAIL


def test_a_detached_descendant_does_not_hold_the_result_hostage(repo):
    started = time.monotonic()
    result = launch(job(repo, {"mode": "escape"}), env=dict(os.environ), timeout=60)
    assert result.status is Status.OK
    assert time.monotonic() - started < 15


def test_a_timeout_with_a_detached_descendant_still_returns_promptly(repo):
    started = time.monotonic()
    result = launch(job(repo, {"mode": "escape_sleep"}), env=dict(os.environ), timeout=3)
    assert result.items[0].error_type == "Timeout"
    assert time.monotonic() - started < 10


def test_dates_in_data_are_passed_as_iso_strings(repo):
    data = {"mode": "when", "when": datetime.date(2026, 1, 1)}
    result = launch(job(repo, data), env=dict(os.environ), timeout=60)
    assert result.items[0].observed == {"when": "2026-01-01"}


def test_data_that_cannot_be_serialized_is_an_error_and_spawns_nothing(repo, monkeypatch):
    def no_spawn(*args, **kwargs):
        raise AssertionError("a worker was spawned")

    monkeypatch.setattr(worker.subprocess, "Popen", no_spawn)
    result = launch(job(repo, {"mode": object()}), env=dict(os.environ), timeout=60)
    assert result.items[0].error_type == "TypeError"


def test_launch_refuses_after_kill_all_until_reset(repo):
    kill_all()
    env = dict(os.environ)
    assert launch(job(repo, {"mode": "fail"}), env=env, timeout=60).items[0].error_type == (
        "Interrupted"
    )
    reset_stop()
    assert launch(job(repo, {"mode": "fail"}), env=env, timeout=60).status is Status.FAIL
