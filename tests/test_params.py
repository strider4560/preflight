import contextlib
from collections.abc import Iterator
from typing import Annotated, Literal

import pytest

from preflight.outcome import fail, outcome
from preflight.params import (
    EMPTY,
    Arg,
    ArgSpec,
    Depends,
    GateDefinitionError,
    Propagate,
    ProviderFailed,
    Resolver,
    Unmet,
    collect_args,
    parameters_of,
    parse_args,
)

Env = Annotated[Literal["dev", "prod"], Arg(help="the environment")]
CALLS: list[tuple[str, str]] = []


def settings(env: Env) -> dict:
    CALLS.append(("settings", env))
    return {"env": env}


Settings = Annotated[dict, Depends(settings)]


def uses_settings(s: Settings, env: Env) -> str:
    return f"{s['env']}-{env}"


def resolver(values=None, overrides=None):
    stack = contextlib.ExitStack()
    return Resolver(values or {}, overrides or {}, stack), stack


def test_every_parameter_needs_arg_or_depends():
    def bad(x: int): ...

    with pytest.raises(GateDefinitionError, match="bad: parameter x needs Annotated"):
        parameters_of(bad)


def test_collect_args_walks_providers_and_merges_by_name():
    specs = collect_args([uses_settings], {})
    assert specs == [ArgSpec("env", Literal["dev", "prod"], EMPTY, "the environment")]


def test_the_same_argument_with_another_type_is_a_gate_error():
    def other(env: Annotated[str, Arg()]): ...

    with pytest.raises(GateDefinitionError, match="argument env is declared twice"):
        collect_args([uses_settings, other], {})


def test_validate_is_reserved():
    def v(validate: Annotated[bool, Arg()] = False): ...

    with pytest.raises(GateDefinitionError, match="reserved"):
        collect_args([v], {})


def test_a_provider_cycle_is_a_gate_error():
    def q() -> int:
        return 0

    def p(v: Annotated[int, Depends(q)]) -> int:
        return v

    def r(v: Annotated[int, Depends(p)]) -> int:
        return v

    with pytest.raises(GateDefinitionError, match="cycle"):
        collect_args([p], {q: r})


SPECS = [
    ArgSpec("env", Literal["dev", "prod"], EMPTY, None),
    ArgSpec("jobs", int, 4, None),
]


def test_parse_args_positional_options_and_validate():
    assert parse_args("gate.py", SPECS, ["dev"]) == ({"env": "dev", "jobs": 4}, False)
    assert parse_args("gate.py", SPECS, ["prod", "--jobs", "2", "--validate"]) == (
        {"env": "prod", "jobs": 2},
        True,
    )


def test_parse_args_refuses_a_bad_choice_with_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("gate.py", SPECS, ["qa"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage: gate.py" in err and "invalid choice: 'qa'" in err


def test_parse_args_refuses_a_value_of_the_wrong_type(capsys):
    with pytest.raises(SystemExit):
        parse_args("gate.py", SPECS, ["dev", "--jobs", "many"])
    assert "argument jobs: invalid value 'many'" in capsys.readouterr().err


def test_providers_resolve_once_and_only_when_needed():
    CALLS.clear()
    r, stack = resolver({"env": "dev"})
    with stack:
        assert CALLS == []
        assert r.arguments(uses_settings) == {"s": {"env": "dev"}, "env": "dev"}
        r.arguments(uses_settings)
    assert CALLS == [("settings", "dev")]


def test_overrides_replace_providers():
    r, stack = resolver({"env": "dev"}, {settings: lambda: {"env": "fake"}})
    with stack:
        assert r.arguments(uses_settings) == {"s": {"env": "fake"}, "env": "dev"}


def test_yield_providers_clean_up_in_reverse_and_failures_are_reported():
    log = []

    def first() -> Iterator[str]:
        log.append("enter first")
        yield "1"
        log.append("exit first")

    def second(f: Annotated[str, Depends(first)]) -> Iterator[str]:
        log.append("enter second")
        yield f + "2"
        raise RuntimeError("secret value")

    def guard(s: Annotated[str, Depends(second)]): ...

    r, stack = resolver()
    assert r.arguments(guard) == {"s": "12"}
    stack.close()
    assert log == ["enter first", "enter second", "exit first"]
    assert r.cleanup_problems == ["provider second cleanup raised RuntimeError"]


def test_unmet_names_its_provider_and_carries_items():
    def signed() -> str:
        raise Unmet(outcome(fail(do="Sign in.")))

    def guard(x: Annotated[str, Depends(signed)]): ...

    r, _ = resolver()
    with pytest.raises(Unmet) as exc:
        r.arguments(guard)
    assert exc.value.provider == "signed"
    assert exc.value.items[0].next_step.do == "Sign in."


def test_unmet_before_yield_in_a_generator_provider():
    def signed() -> Iterator[str]:
        raise Unmet(fail(do="Sign in."))
        yield "never"

    def guard(x: Annotated[str, Depends(signed)]): ...

    r, _ = resolver()
    with pytest.raises(Unmet):
        r.arguments(guard)


def test_other_provider_exceptions_carry_only_their_type_and_the_inner_name():
    def inner() -> str:
        raise KeyError("account_id=123456789012")

    def outer(v: Annotated[str, Depends(inner)]) -> str:
        return v

    def guard(x: Annotated[str, Depends(outer)]): ...

    r, _ = resolver()
    with pytest.raises(ProviderFailed) as exc:
        r.arguments(guard)
    assert str(exc.value) == "provider inner raised KeyError"
    assert "123456789012" not in str(exc.value)


def test_unmet_needs_an_item():
    with pytest.raises(ValueError):
        Unmet()


def test_propagate_passes_through_providers_unwrapped():
    class Stop(Propagate):
        pass

    def stops() -> str:
        raise Stop("no stand-in")

    def guard(x: Annotated[str, Depends(stops)]): ...

    r, _ = resolver()
    with pytest.raises(Stop):
        r.arguments(guard)


def test_literal_choices_must_be_strings():
    def n(level: Annotated[Literal[1, 2], Arg()]): ...

    with pytest.raises(
        GateDefinitionError, match="argument level: Literal choices must be strings"
    ):
        collect_args([n], {})


def test_bool_options_are_switches():
    specs = [SPECS[0], ArgSpec("dry_run", bool, False, None)]
    assert parse_args("gate.py", specs, ["dev", "--dry-run"]) == (
        {"env": "dev", "dry_run": True},
        False,
    )
    assert parse_args("gate.py", specs, ["dev"])[0]["dry_run"] is False
    assert parse_args("gate.py", specs, ["dev", "--no-dry-run"])[0]["dry_run"] is False


def test_option_abbreviations_are_refused():
    with pytest.raises(SystemExit) as exc:
        parse_args("gate.py", SPECS, ["dev", "--val"])
    assert exc.value.code == 2
