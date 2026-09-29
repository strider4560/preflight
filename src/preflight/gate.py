"""Gates: named sets of check instances in the consumer's repository, loaded by file path.

The consumer directory (the parent of `gates/`) is imported as the package `consumer`, so a gate
imports repo-local checks as `consumer.checks.<file>`. Nothing is added to `sys.path`."""

from __future__ import annotations

import importlib
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from preflight.check import CheckInstance

STEM = re.compile(r"^[a-z][a-z0-9_]*$")


class GateError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Gate:
    name: str
    checks: tuple[CheckInstance, ...]
    requires: tuple[str, ...] = ()
    guards: str | None = None
    scope: Literal["environment", "repository"] = "environment"

    def __post_init__(self) -> None:
        if not isinstance(self.requires, (list, tuple)) or not all(
            isinstance(r, str) and STEM.match(r) for r in self.requires
        ):
            raise GateError(
                [f"gate {self.name}: requires must be a list of gate names (lowercase file stems)"]
            )
        if self.guards is not None and not isinstance(self.guards, str):
            raise GateError([f"gate {self.name}: guards must be a string or None"])
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "requires", tuple(self.requires))
        if not self.checks:
            raise GateError([f"gate {self.name} has no checks"])
        for instance in self.checks:
            if not isinstance(instance, CheckInstance):
                raise GateError(
                    [
                        f"gate {self.name}: {instance!r} is not bound; "
                        "call the check with a section name"
                    ]
                )
        if self.scope not in ("environment", "repository"):
            raise GateError([f"gate {self.name}: scope must be environment or repository"])

    @property
    def milestone(self) -> bool:
        return self.guards is None


def register_consumer(consumer_dir: Path) -> None:
    consumer_dir = consumer_dir.resolve()
    existing = sys.modules.get("consumer")
    if existing is not None:
        if list(getattr(existing, "__path__", [])) == [str(consumer_dir)]:
            return
        raise GateError([f"another consumer directory is already loaded: {existing.__path__[0]}"])
    package = types.ModuleType("consumer")
    package.__path__ = [str(consumer_dir)]
    package.__package__ = "consumer"
    sys.modules["consumer"] = package
    importlib.invalidate_caches()


def unload_consumer() -> None:
    for name in [n for n in sys.modules if n == "consumer" or n.startswith("consumer.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def load_gate_file(path: Path) -> Gate:
    path = path.resolve()
    if path.suffix != ".py" or path.parent.name != "gates":
        raise GateError([f"{path}: a gate is a .py file in a gates/ directory"])
    if not STEM.match(path.stem):
        raise GateError([f"{path.name}: gate file names are lowercase letters, digits and _"])
    register_consumer(path.parent.parent)
    try:
        module = importlib.import_module(f"consumer.gates.{path.stem}")
    except SyntaxError as exc:
        raise GateError(
            [f"{path.name}: cannot be loaded (SyntaxError at line {exc.lineno})"]
        ) from None
    except (Exception, SystemExit) as exc:
        # Only the type: an exception's message may carry a value the gate file read.
        raise GateError([f"{path.name}: cannot be loaded ({type(exc).__name__})"]) from None
    gate = getattr(module, "gate", None)
    if not isinstance(gate, Gate):
        raise GateError([f"{path.name}: defines no `gate = Gate(...)`"])
    if gate.name != path.stem:
        raise GateError(
            [f"{path.name}: the gate is named {gate.name!r}; name it {path.stem!r} after its file"]
        )
    return gate


def load_closure(path: Path) -> list[Gate]:
    """The gate and every gate it requires, transitively, required gates first."""
    gates_dir = path.resolve().parent
    first = load_gate_file(path)
    found = {first.name: first}
    pending = list(first.requires)
    problems = []
    while pending:
        name = pending.pop()
        if name in found:
            continue
        candidate = gates_dir / f"{name}.py"
        if not candidate.is_file():
            problems.append(f"gate {name!r} is required but {candidate.name} does not exist")
            continue
        found[name] = load_gate_file(candidate)
        pending.extend(found[name].requires)
    if problems:
        raise GateError(problems)
    return order_gates(list(found.values()))


def load_directory(gates_dir: Path) -> list[Gate]:
    files = sorted(p for p in gates_dir.glob("*.py") if not p.name.startswith("_"))
    if not files:
        raise GateError([f"{gates_dir} holds no gate files"])
    gates, problems = [], []
    for path in files:
        try:
            gates.append(load_gate_file(path))
        except GateError as exc:
            problems.extend(exc.problems)
    names = {g.name for g in gates}
    for gate in gates:
        problems.extend(
            f"gate {gate.name} requires unknown gate {r!r}" for r in gate.requires if r not in names
        )
    if problems:
        raise GateError(problems)
    return order_gates(gates)


def order_gates(gates: list[Gate]) -> list[Gate]:
    by_name = {g.name: g for g in gates}
    state: dict[str, str] = {}
    ordered: list[Gate] = []

    def visit(gate: Gate, trail: list[str]) -> None:
        if state.get(gate.name) == "done":
            return
        if state.get(gate.name) == "active":
            cycle = " -> ".join([*trail, gate.name])
            raise GateError([f"gates require each other in a cycle: {cycle}"])
        state[gate.name] = "active"
        for required in sorted(gate.requires):
            if required in by_name:
                visit(by_name[required], [*trail, gate.name])
        state[gate.name] = "done"
        ordered.append(gate)

    for gate in sorted(gates, key=lambda g: g.name):
        visit(gate, [])
    return ordered
