# src/preflight/graph.py
"""The instance graph: every gate's instances plus their implicit prerequisites (sessions,
SSM parameters, placeholders), in dependency order."""

from __future__ import annotations

import heapq
import re
import string
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from preflight.check import REGISTRY, CheckInstance, field_problems
from preflight.contract import Contract, Placeholder
from preflight.gate import Gate
from preflight.outcome import Outcome, fail, outcome
from preflight.resolvers import LazySsm


class GraphError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class Node:
    id: str
    check_id: str
    key: str
    gates: list[str]
    contract: Contract
    data: dict[str, Any]
    identity: str | None
    requires: list[str] = field(default_factory=list)
    timeout: float = 60.0
    static: Outcome | None = None
    order: int = 0
    section_bound: bool = True

    @property
    def owner(self) -> str:
        return self.gates[0]

    def remedy(self) -> dict[str, str]:
        remedy = self.data.get("remedy") if self.section_bound else None
        return (
            {k: v for k, v in remedy.items() if isinstance(v, str)}
            if isinstance(remedy, dict)
            else {}
        )

    def template_values(self) -> dict[str, Any]:
        if self.section_bound:
            return self.contract.template_values(self.key)
        return {"environment": self.contract.environment or ""}


@dataclass
class Plan:
    nodes: dict[str, Node]
    gates: list[Gate]
    contracts: dict[str, Contract]

    def ordered(self) -> list[Node]:
        return list(self.nodes.values())

    def nodes_of(self, gate: str) -> list[Node]:
        return [node for node in self.nodes.values() if gate in node.gates]

    @property
    def inputs(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for contract in self.contracts.values():
            merged.update(contract.inputs)
        return merged


def iter_lazy(value: Any) -> Iterator[LazySsm]:
    if isinstance(value, LazySsm):
        yield value
    elif isinstance(value, dict):
        for element in value.values():
            yield from iter_lazy(element)
    elif isinstance(value, list):
        for element in value:
            yield from iter_lazy(element)


def _template_fields(text: str) -> list[str]:
    """The top-level names a format template refers to; none when it cannot be parsed."""
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        return []
    return [re.split(r"[.\[]", field, maxsplit=1)[0] for _, field, _, _ in parsed if field]


class _Builder:
    def __init__(self, contracts: Mapping[str, Contract]):
        self.contracts = contracts
        self.primary = "environment" if "environment" in contracts else "repository"
        self.nodes: dict[str, Node] = {}
        self.problems: list[str] = []
        self.bound: dict[tuple[str, str], list[type]] = {}

    def _id(self, scope: str, instance_id: str) -> str:
        return instance_id if scope == self.primary else f"{scope}/{instance_id}"

    def _join(self, node_id: str, gate: Gate) -> None:
        node = self.nodes[node_id]
        if gate.name in node.gates:
            return
        node.gates.append(gate.name)
        for dependency in node.requires:
            self._join(dependency, gate)

    def _new(self, node_id: str, **fields: Any) -> Node:
        node = Node(node_id, order=len(self.nodes), **fields)
        self.nodes[node_id] = node
        return node

    def add(self, instance: CheckInstance, gate: Gate) -> str | None:
        contract = self.contracts[gate.scope]
        check = instance.check
        node_id = self._id(gate.scope, instance.id)
        if node_id in self.nodes:
            self._join(node_id, gate)
            return node_id
        where = f"{gate.name}: {instance.id}"
        if check.binds == "identity":
            if instance.key not in contract.identity_data:
                self.problems.append(
                    f"{where}: no [identities.{instance.key}] in {contract.path.name}"
                )
                return None
            node = self._new(
                node_id,
                check_id=check.id,
                key=instance.key,
                gates=[gate.name],
                contract=contract,
                data={"identity": instance.key},
                identity=instance.key,
                timeout=check.timeout,
                section_bound=False,
            )
            owner = f"identities.{instance.key}"
        else:
            if instance.key not in contract.sections:
                self.problems.append(
                    f"{where}: no section [{instance.key}] in {contract.path.name}"
                )
                return None
            data = contract.sections[instance.key]
            self.bound.setdefault((gate.scope, instance.key), []).append(check.section)
            skip = contract.placeholder_fields(instance.key) | contract.lazy_fields(instance.key)
            self.problems.extend(
                f"[{instance.key}] for {check.id}: {problem}"
                for problem in field_problems(check.section, data, skip)
            )
            identity = data.get("identity") if isinstance(data.get("identity"), str) else None
            if identity is not None and "identity" in contract.placeholder_fields(instance.key):
                identity = None
            elif identity is not None and identity not in contract.identity_data:
                self.problems.append(
                    f"[{instance.key}].identity: no [identities.{identity}] in {contract.path.name}"
                )
                identity = None
            self._check_remedy(contract, instance.key, data)
            timeout = data.get("timeout")
            node = self._new(
                node_id,
                check_id=check.id,
                key=instance.key,
                gates=[gate.name],
                contract=contract,
                data=data,
                identity=identity,
                timeout=float(timeout) if isinstance(timeout, (int, float)) else check.timeout,
            )
            owner = instance.key
        for requirement in check.requires:
            required = REGISTRY.get(requirement.check_id)
            if required is None or required.binds != "identity":
                self.problems.append(
                    f"{check.id}: requires unknown identity check {requirement.check_id}"
                )
                continue
            alias = node.data.get(requirement.field)
            if not isinstance(alias, str):
                continue
            if alias in contract.identity_data:
                dependency = self.add(required(alias), gate)
                if dependency and dependency not in node.requires:
                    node.requires.append(dependency)
            elif requirement.field not in contract.placeholder_fields(owner):
                self.problems.append(
                    f"[{instance.key}].{requirement.field}: no [identities.{alias}] "
                    f"in {contract.path.name}"
                )
        if node.section_bound and node.identity is not None:
            dependency = self.add(REGISTRY["aws.session"](node.identity), gate)
            if dependency and dependency not in node.requires:
                node.requires.append(dependency)
        for lazy in iter_lazy(node.data):
            dependency = self._ssm_present(lazy, gate)
            if dependency and dependency not in node.requires:
                node.requires.append(dependency)
        for placeholder in contract.placeholders:
            if placeholder.owner == owner:
                dependency = self._placeholder(placeholder, gate)
                if dependency not in node.requires:
                    node.requires.append(dependency)
        return node_id

    def _ssm_present(self, lazy: LazySsm, gate: Gate) -> str | None:
        contract = self.contracts[gate.scope]
        if lazy.identity not in contract.identity_data:
            return None  # load_contract already reported it
        node_id = self._id(gate.scope, f"ssm.present[{lazy.name}]")
        if node_id in self.nodes:
            self._join(node_id, gate)
            return node_id
        check = REGISTRY["ssm.present"]
        node = self._new(
            node_id,
            check_id=check.id,
            key=lazy.name,
            gates=[gate.name],
            contract=contract,
            data={"identity": lazy.identity, "name": lazy.name, "how": lazy.how},
            identity=lazy.identity,
            timeout=check.timeout,
            section_bound=False,
        )
        session = self.add(REGISTRY["aws.session"](lazy.identity), gate)
        if session:
            node.requires.append(session)
        return node_id

    def _placeholder(self, placeholder: Placeholder, gate: Gate) -> str:
        node_id = self._id(gate.scope, placeholder.id)
        if node_id in self.nodes:
            self._join(node_id, gate)
            return node_id
        step = placeholder.next_step()
        self._new(
            node_id,
            check_id="contract.placeholder",
            key=placeholder.path,
            gates=[gate.name],
            contract=self.contracts[gate.scope],
            data={},
            identity=None,
            static=outcome(fail(do=step.do, paste=step.paste)),
            section_bound=False,
        )
        return node_id

    def _check_remedy(self, contract: Contract, section: str, data: dict[str, Any]) -> None:
        remedy = data.get("remedy")
        if not isinstance(remedy, dict):
            return
        values = contract.template_values(section)
        withheld = contract.placeholder_fields(section) | contract.lazy_fields(section)
        for name, text in remedy.items():
            if not isinstance(text, str):
                continue
            try:
                text.format_map(values)
            except Exception:
                named = next((f for f in _template_fields(text) if f in withheld), None)
                problem = (
                    f"{{{named}}} is not available in remedies (placeholder or ssm value)"
                    if named
                    else "malformed or unknown template field"
                )
                self.problems.append(f"[{section}.remedy].{name}: {problem}")

    def link(self, gates: Sequence[Gate]) -> None:
        by_name = {gate.name: gate for gate in gates}

        def closure(names: Sequence[str]) -> set[str]:
            found: set[str] = set()
            pending = list(names)
            while pending:
                name = pending.pop()
                if name in found or name not in by_name:
                    continue
                found.add(name)
                pending.extend(by_name[name].requires)
            return found

        for gate in gates:
            required = closure(gate.requires)
            if not required:
                continue
            earlier = [n.id for n in self.nodes.values() if required & set(n.gates)]
            for node in self.nodes.values():
                if gate.name in node.gates and not required & set(node.gates):
                    node.requires.extend(d for d in earlier if d not in node.requires)

    def strict(self) -> None:
        contract = self.contracts[self.primary]
        for name, data in contract.sections.items():
            models = self.bound.get((self.primary, name))
            if not models:
                self.problems.append(f"[{name}] in {contract.path.name} is used by no gate")
                continue
            declared = set().union(*(model.model_fields for model in models))
            self.problems.extend(
                f"[{name}].{key} is used by no check bound to [{name}]"
                for key in data
                if key not in declared
            )

    def topological(self) -> list[Node]:
        indegree = dict.fromkeys(self.nodes, 0)
        dependents: dict[str, list[str]] = {node_id: [] for node_id in self.nodes}
        for node in self.nodes.values():
            for dependency in node.requires:
                if dependency in self.nodes:
                    indegree[node.id] += 1
                    dependents[dependency].append(node.id)
        ready = [(node.order, node.id) for node in self.nodes.values() if indegree[node.id] == 0]
        heapq.heapify(ready)
        ordered = []
        while ready:
            _, node_id = heapq.heappop(ready)
            ordered.append(self.nodes[node_id])
            for child in dependents[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, (self.nodes[child].order, child))
        if len(ordered) != len(self.nodes):
            stuck = sorted(node_id for node_id, count in indegree.items() if count > 0)
            self.problems.append(f"prerequisite cycle among: {', '.join(stuck)}")
        return ordered


def build_plan(
    gates: Sequence[Gate],
    contracts: Mapping[str, Contract],
    *,
    link_gates: bool,
    strict: bool,
) -> Plan:
    import preflight.catalog.aws  # noqa: F401  registers aws.session
    import preflight.catalog.ssm  # noqa: F401  registers ssm.present

    builder = _Builder(contracts)
    for gate in gates:
        if gate.scope not in contracts:
            builder.problems.append(f"gate {gate.name} needs a {gate.scope} contract")
            continue
        for instance in gate.checks:
            builder.add(instance, gate)
    if link_gates:
        builder.link(gates)
    if strict:
        builder.strict()
    ordered = builder.topological()
    problems = list(dict.fromkeys(builder.problems))
    if problems:
        raise GraphError(problems)
    return Plan({node.id: node for node in ordered}, list(gates), dict(contracts))
