import threading
import time
from pathlib import Path

import pytest

from preflight import runner
from preflight.contract import Contract
from preflight.graph import Node, Plan
from preflight.outcome import Status, fail, ok, outcome


def contract(sections=None):
    return Contract(
        path=Path("dev.toml"),
        root=Path("/tmp"),
        scope="environment",
        environment="dev",
        identity_data={},
        sections=sections or {},
        placeholders=[],
        inputs={},
    )


CONTRACT = contract()


def node(node_id, *requires, static=None, bound=False, c=CONTRACT, data=None):
    return Node(
        id=node_id,
        check_id="graph.thing",
        key=node_id,
        gates=["g"],
        contract=c,
        data=data or {},
        identity=None,
        requires=list(requires),
        static=static,
        section_bound=bound,
    )


def plan(*nodes):
    return Plan({n.id: n for n in nodes}, [], {"environment": CONTRACT})


def scripted(outcomes):
    calls = []

    def launch(n):
        calls.append(n.id)
        return outcomes[n.id]

    launch.calls = calls
    return launch


def test_dependents_of_a_failure_are_blocked_and_not_run():
    launch = scripted({"a": outcome(fail(do="x")), "b": outcome(ok()), "c": outcome(ok())})
    results = runner.run_plan(plan(node("a"), node("b", "a"), node("c", "b")), launcher=launch)
    assert [r.status for r in results.values()] == [Status.FAIL, Status.BLOCKED, Status.BLOCKED]
    assert results["b"].blocked_by == ["a"]
    assert results["c"].blocked_by == ["b"]
    assert launch.calls == ["a"]


def test_an_advisory_failure_does_not_block():
    launch = scripted({"a": outcome(fail("w", do="x", advisory=True)), "b": outcome(ok())})
    results = runner.run_plan(plan(node("a"), node("b", "a")), launcher=launch)
    assert results["b"].status is Status.OK


def test_static_outcomes_are_not_launched():
    launch = scripted({})
    results = runner.run_plan(
        plan(node("p", static=outcome(fail(do="Fill it."))), node("b", "p")), launcher=launch
    )
    assert [r.status for r in results.values()] == [Status.FAIL, Status.BLOCKED]
    assert launch.calls == []


def test_the_sections_remedy_is_applied():
    c = contract({"s": {"remedy": {"do": "Rerun for {environment}."}}})
    launch = scripted({"s": outcome(fail(do="generic", generic=True))})
    n = node("s", bound=True, c=c, data=c.sections["s"])
    result = runner.run_plan(Plan({"s": n}, [], {"environment": c}), launcher=launch)["s"]
    assert result.outcome.items[0].next_step.do == "Rerun for dev."


def test_no_more_than_jobs_run_at_once():
    active = 0
    peak = 0
    lock = threading.Lock()

    def launch(n):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return outcome(ok())

    runner.run_plan(plan(*[node(f"n{i}") for i in range(6)]), jobs=2, launcher=launch)
    assert peak == 2


def test_an_interrupt_kills_the_workers(monkeypatch):
    killed = []
    monkeypatch.setattr(runner, "kill_all", lambda: killed.append(True))

    def launch(n):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_plan(plan(node("a")), launcher=launch)
    assert killed == [True]
