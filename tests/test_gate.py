import sys

import pytest
from conftest import write

from preflight import Section, check, ok, outcome
from preflight.gate import Gate, GateError, load_closure, load_directory, load_gate_file


class Local(Section):
    items: list[str] = []


@check("gate_test.local", section=Local)
def local(ctx, s):
    return outcome(ok())


CHECK = """
from preflight import Section, check, ok, outcome


class Things(Section):
    items: list[str] = []


@check("sample.thing", section=Things)
def thing(ctx, s):
    return outcome(ok())
"""

GATE = """
from preflight import Gate
from consumer.checks.sample import thing

gate = Gate("{name}", checks=[thing("things")], requires={requires!r})
"""


def consumer(repo, gates):
    write(repo, "preflight/checks/sample.py", CHECK)
    for name, requires in gates.items():
        write(repo, f"preflight/gates/{name}.py", GATE.format(name=name, requires=requires))
    return repo / "preflight" / "gates"


def test_loads_a_gate_and_its_required_gates_in_order(repo):
    gates = consumer(repo, {"a": [], "b": ["a"], "c": ["b"]})
    assert [g.name for g in load_closure(gates / "c.py")] == ["a", "b", "c"]


def test_a_gate_is_named_after_its_file(repo):
    gates = consumer(repo, {"a": []})
    (gates / "x.py").write_text(GATE.format(name="y", requires=[]))
    with pytest.raises(GateError, match="name it 'x'"):
        load_gate_file(gates / "x.py")


def test_a_missing_required_gate(repo):
    gates = consumer(repo, {"b": ["a"]})
    with pytest.raises(GateError, match="a.py does not exist"):
        load_closure(gates / "b.py")


def test_a_cycle_between_gates(repo):
    gates = consumer(repo, {"a": ["b"], "b": ["a"]})
    with pytest.raises(GateError, match="cycle"):
        load_directory(gates)


def test_a_syntax_error_names_the_file(repo):
    gates = consumer(repo, {"a": []})
    (gates / "broken.py").write_text("gate = (\n")
    with pytest.raises(GateError) as caught:
        load_gate_file(gates / "broken.py")
    assert "broken.py" in caught.value.problems[0]
    assert "SyntaxError" in caught.value.problems[0]


def test_a_gate_named_like_a_stdlib_module_does_not_shadow_it(repo):
    gates = consumer(repo, {"secrets": []})
    before = list(sys.path)
    load_gate_file(gates / "secrets.py")
    assert sys.path == before
    import secrets

    assert hasattr(secrets, "token_hex")


def test_load_directory_orders_by_requires_and_skips_private_files(repo):
    gates = consumer(repo, {"b": ["a"], "a": []})
    (gates / "_helpers.py").write_text("x = 1\n")
    assert [g.name for g in load_directory(gates)] == ["a", "b"]


def test_a_gate_needs_check_instances():
    with pytest.raises(GateError, match="has no checks"):
        Gate("x", checks=[])


def test_sys_exit_in_a_gate_file_is_a_gate_error(repo):
    gates = consumer(repo, {"a": []})
    (gates / "quits.py").write_text("import sys\nsys.exit(3)\n")
    with pytest.raises(GateError) as caught:
        load_gate_file(gates / "quits.py")
    assert "quits.py" in caught.value.problems[0]
    assert "SystemExit" in caught.value.problems[0]


@pytest.mark.parametrize(
    "kwargs",
    [{"requires": "abc"}, {"requires": [1]}, {"requires": ["b", 1]}, {"guards": 3}],
)
def test_requires_and_guards_are_validated(kwargs):
    with pytest.raises(GateError):
        Gate("x", checks=[local("things")], **kwargs)


def test_a_bare_check_is_refused(repo):
    consumer(repo, {"a": []})
    from preflight.gate import register_consumer

    register_consumer(repo / "preflight")
    from consumer.checks.sample import thing

    with pytest.raises(GateError, match="call the check with a section name"):
        Gate("x", checks=[thing])
