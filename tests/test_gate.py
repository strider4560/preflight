import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

import pytest

from preflight import gate as gate_module
from preflight.check import check
from preflight.gate import Gate, Guards
from preflight.outcome import Outcome, fail, ok, outcome
from preflight.params import Arg, Depends, Unmet
from preflight.probe import Probe

Env = Annotated[Literal["dev", "prod"], Arg()]


@check(key="name")
def thing(probe: Probe, name: str) -> Outcome:
    return outcome(ok())


class Scripted:
    """Answers checks by label; records what ran."""

    def __init__(self, answers=None, raise_on=None, exc=None):
        self.answers = answers or {}
        self.ran: list[str] = []
        self.raise_on, self.exc = raise_on, exc

    def run(self, checks):
        self.ran.extend(c.label for c in checks)
        if self.raise_on in self.ran:
            raise self.exc
        return [self.answers.get(c.label, outcome(ok())) for c in checks]


def three_guards():
    gate = Gate("sample")

    @gate.guard("first")
    def first(env: Env):
        return [thing(name=f"{env}-1")]

    @gate.guard("second")
    def second():
        return [thing(name="2a"), thing(name="2b")]

    @gate.guard("third")
    def third():
        return [thing(name="3")]

    return gate


def run(gate, argv=("dev",), executor=None, tmp=Path(".")):
    return gate.execute(list(argv), executor=executor or Scripted(), root=tmp)


def test_guards_run_in_order_and_everything_passing_exits_0(tmp_path):
    executor = Scripted()
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 0
    assert executor.ran == [
        "test_gate.thing(dev-1)",
        "test_gate.thing(2a)",
        "test_gate.thing(2b)",
        "test_gate.thing(3)",
    ]
    assert result.output.endswith("Every guard passed.\n")


def test_the_first_guard_that_does_not_pass_stops_the_run(tmp_path):
    executor = Scripted({"test_gate.thing(2b)": outcome(fail(do="Fix 2b."))})
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 1
    assert result.stopped_at == "second"
    assert result.not_run == ("third",)
    assert "test_gate.thing(3)" not in executor.ran
    assert "Fix 2b." in result.output


def test_include_places_guards_where_it_is_called(tmp_path):
    shared = Guards()

    @shared.guard("shared")
    def shared_guard():
        return [thing(name="s")]

    gate = Gate("g")

    @gate.guard("before")
    def before():
        return [thing(name="b")]

    gate.include(shared)

    @gate.guard("after")
    def after():
        return [thing(name="a")]

    executor = Scripted()
    run(gate, argv=(), executor=executor, tmp=tmp_path)
    assert executor.ran == ["test_gate.thing(b)", "test_gate.thing(s)", "test_gate.thing(a)"]


def test_repeated_labels_are_numbered(tmp_path):
    gate = Gate("g")

    @gate.guard("twice")
    def twice():
        return [thing(name="x"), thing(name="x")]

    executor = Scripted({"test_gate.thing(x)": outcome(fail(do="No."))})
    result = run(gate, argv=(), executor=executor, tmp=tmp_path)
    labels = [c.label for c in result.guards[0].checks]
    assert labels == ["test_gate.thing(x) #1", "test_gate.thing(x) #2"]


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (lambda: thing(name="x"), "guard 'bad' must return a non-empty list of checks"),
        (lambda: [], "guard 'bad' must return a non-empty list of checks"),
        (lambda: [thing(name=3)], "guard 'bad': test_gate.thing: name: Input should be"),
        (lambda: {}["missing"], "guard 'bad' raised KeyError"),
    ],
)
def test_a_wrong_guard_is_a_gate_error(tmp_path, body, problem):
    gate = Gate("g")
    gate.guard("bad")(body)
    executor = Scripted()
    result = run(gate, argv=(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 2
    assert any(p.startswith(problem) for p in result.problems)
    assert executor.ran == []


def test_definition_errors_exit_2_before_anything_runs(tmp_path):
    gate = Gate("g")
    gate.guard("same")(lambda: [thing(name="1")])
    gate.guard("same")(lambda: [thing(name="2")])
    assert run(gate, argv=(), tmp=tmp_path).problems == ("guard name used twice: same",)
    empty = Gate("empty")
    assert run(empty, argv=(), tmp=tmp_path).problems == ("gate empty has no guards",)

    def untyped(x):
        return [thing(name="x")]

    loose = Gate("loose")
    loose.guard("loose")(untyped)
    result = run(loose, argv=(), tmp=tmp_path)
    assert result.exit_code == 2
    assert "parameter x needs Annotated" in result.problems[0]


def test_a_bad_command_line_exits_2_without_running(tmp_path, capsys):
    executor = Scripted()
    result = run(three_guards(), argv=("qa",), executor=executor, tmp=tmp_path)
    assert (result.exit_code, executor.ran) == (2, [])
    assert "invalid choice: 'qa'" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_2_so_a_wrapper_never_proceeds(tmp_path, capsys, flag):
    executor = Scripted()
    result = run(three_guards(), argv=(flag,), executor=executor, tmp=tmp_path)
    assert (result.exit_code, executor.ran) == (2, [])
    assert "usage:" in capsys.readouterr().out


def test_provider_and_guard_exceptions_report_only_their_type(tmp_path):
    def admin(env: Env) -> str:
        raise KeyError("account_id=123456789012")

    gate = Gate("g")

    @gate.guard("needs admin")
    def needs(identity: Annotated[str, Depends(admin)]):
        return [thing(name=identity)]

    result = run(gate, tmp=tmp_path)
    assert result.exit_code == 2
    assert result.problems == ("guard 'needs admin': provider admin raised KeyError",)
    assert "123456789012" not in result.output


def test_unmet_stops_the_run_with_the_providers_steps(tmp_path):
    def admin() -> str:
        raise Unmet(fail(do="Sign in to profile sandbox."))

    gate = Gate("g")

    @gate.guard("needs admin")
    def needs(identity: Annotated[str, Depends(admin)]):
        return [thing(name=identity)]

    result = run(gate, argv=(), tmp=tmp_path)
    assert (result.exit_code, result.stopped_at) == (1, "needs admin")
    assert "FAIL    needs admin\n              Sign in to profile sandbox." in result.output


def cleaned_gate(log, cleanup_raises=False):
    def session() -> Iterator[str]:
        log.append("enter")
        yield "s"
        log.append("exit")
        if cleanup_raises:
            raise RuntimeError("secret")

    gate = Gate("g")

    @gate.guard("uses session")
    def first(s: Annotated[str, Depends(session)]):
        return [thing(name="1")]

    @gate.guard("second")
    def second():
        return [thing(name="2")]

    return gate


def test_cleanup_runs_on_pass_and_on_stop(tmp_path):
    log = []
    assert run(cleaned_gate(log), argv=(), tmp=tmp_path).exit_code == 0
    assert log == ["enter", "exit"]
    log.clear()
    stop = Scripted({"test_gate.thing(2)": outcome(fail(do="x"))})
    assert run(cleaned_gate(log), argv=(), executor=stop, tmp=tmp_path).exit_code == 1
    assert log == ["enter", "exit"]


def test_a_failing_cleanup_turns_a_pass_into_3_and_leaves_a_stop_at_1(tmp_path):
    passed = run(cleaned_gate([], cleanup_raises=True), argv=(), tmp=tmp_path)
    assert passed.exit_code == 3
    assert passed.problems == ("provider session cleanup raised RuntimeError",)
    stop = Scripted({"test_gate.thing(2)": outcome(fail(do="x"))})
    stopped = run(cleaned_gate([], cleanup_raises=True), argv=(), executor=stop, tmp=tmp_path)
    assert stopped.exit_code == 1


def test_an_interrupt_kills_workers_cleans_up_and_exits_130(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(gate_module, "kill_all", lambda: killed.append(True))
    log = []
    executor = Scripted(raise_on="test_gate.thing(1)", exc=KeyboardInterrupt())
    result = run(cleaned_gate(log), argv=(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 130
    assert killed == [True]
    assert log == ["enter", "exit"]
    assert result.problems == ("interrupted during guard 'uses session'",)
    assert result.not_run == ("second",)
    assert "Every guard passed" not in result.output


def test_an_unexpected_executor_failure_is_a_preflight_bug(tmp_path):
    executor = Scripted(raise_on="test_gate.thing(dev-1)", exc=RuntimeError("secret"))
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 3
    assert result.problems == ("preflight failed in guard 'first' (RuntimeError)",)
    assert result.not_run == ("second", "third")


def test_validate_observes_nothing_and_exits_0(tmp_path):
    executor = Scripted({"test_gate.thing(dev-1)": outcome(fail(do="x"))})
    result = run(three_guards(), argv=("dev", "--validate"), executor=executor, tmp=tmp_path)
    assert (result.exit_code, executor.ran) == (0, [])
    assert result.validated
    assert result.output.endswith("Validated every guard; nothing was observed.\n")


def test_the_gate_knows_its_file_and_run_exits_with_the_code(tmp_path, capsys, monkeypatch):
    gate = three_guards()
    assert gate.file == Path(__file__).resolve()
    monkeypatch.setattr(gate, "execute", lambda argv, **kw: run(three_guards(), tmp=tmp_path))
    with pytest.raises(SystemExit) as exc:
        gate.run(["dev"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.endswith("Every guard passed.\n")


def test_a_sys_exit_in_a_guard_is_a_gate_error_and_cleanup_still_runs(tmp_path):
    log = []

    def session() -> Iterator[str]:
        log.append("enter")
        yield "s"
        log.append("exit")

    gate = Gate("g")

    @gate.guard("uses session")
    def first(s: Annotated[str, Depends(session)]):
        return [thing(name="1")]

    @gate.guard("exits")
    def exits():
        sys.exit(0)

    result = run(gate, argv=(), tmp=tmp_path)
    assert result.exit_code == 2
    assert "guard 'exits' raised SystemExit" in result.problems
    assert log == ["enter", "exit"]


def test_a_sys_exit_in_a_provider_is_a_gate_error(tmp_path):
    def quitter() -> str:
        sys.exit(0)

    gate = Gate("g")

    @gate.guard("needs it")
    def needs(x: Annotated[str, Depends(quitter)]):
        return [thing(name=x)]

    result = run(gate, argv=(), tmp=tmp_path)
    assert result.exit_code == 2
    assert result.problems == ("guard 'needs it': provider quitter raised SystemExit",)


def test_run_exits_130_when_interrupted_outside_the_guard_loop(capsys, monkeypatch):
    gate = three_guards()

    def interrupted(argv, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(gate, "execute", interrupted)
    with pytest.raises(SystemExit) as exc:
        gate.run(["dev"])
    assert exc.value.code == 130
    assert capsys.readouterr().out == ""
