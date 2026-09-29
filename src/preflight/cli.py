"""preflight check | status | validate.

`check` runs one gate (and the gates it requires) against a contract and exits 0 only when
every blocking item is ok. A consumer's script calls it first and stops on anything else."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from preflight.contract import Contract, ContractError, load_contract, repo_root
from preflight.gate import Gate, GateError, load_closure, load_directory, load_gate_file
from preflight.graph import GraphError, build_plan
from preflight.outcome import Status
from preflight.render import (
    open_steps,
    render_check,
    render_status,
    report_json,
    run_json,
    to_junit,
    write_json,
)
from preflight.runner import run_plan


class Invalid(Exception):
    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


INVALID = (ContractError, GateError, GraphError, Invalid)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _report_invalid(exc: Exception) -> int:
    print("preflight: cannot run:", file=sys.stderr)
    for problem in getattr(exc, "problems", [str(exc)]):
        print(f"  - {problem}", file=sys.stderr)
    return 2


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _contracts_for(gates: list[Gate], contract: Contract) -> dict[str, Contract]:
    contracts = {contract.scope: contract}
    scopes = {gate.scope for gate in gates}
    if "repository" in scopes and "repository" not in contracts:
        path = contract.path.parent / "repo.toml"
        if not path.is_file():
            raise Invalid([f"gates with repository scope need {path}, which does not exist"])
        repository = load_contract(path)
        if repository.scope != "repository":
            raise Invalid([f'{path} must have scope = "repository"'])
        contracts["repository"] = repository
    if "environment" in scopes and "environment" not in contracts:
        raise Invalid(["a repository gate cannot require an environment gate"])
    return contracts


def _with_requirements(gates: list[Gate], chosen: list[Gate]) -> list[Gate]:
    by_name = {gate.name: gate for gate in gates}
    names: set[str] = set()
    pending = [gate.name for gate in chosen]
    while pending:
        name = pending.pop()
        if name in names or name not in by_name:
            continue
        names.add(name)
        pending.extend(by_name[name].requires)
    return [gate for gate in gates if gate.name in names]


def _defaults(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.gates and args.contracts:
        return Path(args.gates), Path(args.contracts)
    root = repo_root(Path.cwd())
    gates = Path(args.gates) if args.gates else root / "preflight" / "gates"
    contracts = Path(args.contracts) if args.contracts else root / "preflight" / "contracts"
    return gates, contracts


def _load_contracts(directory: Path) -> tuple[Contract | None, list[Contract]]:
    paths = sorted(directory.glob("*.toml"))
    if not paths:
        raise Invalid([f"{directory} holds no contracts"])
    contracts, problems = [], []
    for path in paths:
        try:
            contracts.append(load_contract(path))
        except ContractError as exc:
            problems.extend(f"{path.name}: {problem}" for problem in exc.problems)
    if problems:
        raise Invalid(problems)
    repository = [c for c in contracts if c.scope == "repository"]
    if len(repository) > 1:
        raise Invalid(["only one repository contract is allowed"])
    environments = sorted(
        (c for c in contracts if c.scope == "environment"), key=lambda c: c.environment or ""
    )
    return (repository[0] if repository else None), environments


def _validate_all(
    gates: list[Gate], repository: Contract | None, environments: list[Contract]
) -> None:
    problems = []
    repository_gates = [g.name for g in gates if g.scope == "repository"]
    if repository_gates and repository is None:
        problems.append(
            f"gate(s) {', '.join(repository_gates)} have repository scope; "
            'add a contract with scope = "repository"'
        )
    environment_gates = [g.name for g in gates if g.scope == "environment"]
    if environment_gates and not environments:
        problems.append(
            f"environment gate(s) {', '.join(environment_gates)} have no environment contract"
        )
    for contract in ([repository] if repository else []) + environments:
        scoped = [g for g in gates if g.scope == contract.scope]
        if not scoped:
            continue
        contracts = {contract.scope: contract}
        if repository is not None:
            contracts.setdefault("repository", repository)
        try:
            build_plan(_with_requirements(gates, scoped), contracts, link_gates=True, strict=True)
        except GraphError as exc:
            problems.extend(f"{contract.path.name}: {problem}" for problem in exc.problems)
    if problems:
        raise Invalid(problems)


def cmd_check(args: argparse.Namespace) -> int:
    started = _now()
    try:
        contract = load_contract(args.contract)
        target = load_gate_file(args.gate)
        gates = load_closure(args.gate)
        if target.scope != contract.scope:
            raise Invalid(
                [
                    f"gate {target.name} has {target.scope} scope, but "
                    f"{contract.path.name} is a {contract.scope} contract"
                ]
            )
        contracts = _contracts_for(gates, contract)
        plan = build_plan(gates, contracts, link_gates=True, strict=False)
    except INVALID as exc:
        return _report_invalid(exc)
    results = run_plan(plan, jobs=args.jobs)
    changed = sorted({path for c in contracts.values() for path in c.changed_inputs()})
    passed = all(r.status is Status.OK for r in results.values()) and not changed
    exit_code = 0 if passed else 1
    title = f"preflight check {target.name}"
    if contract.environment:
        title += f" ({contract.environment})"
    sys.stdout.write(render_check(title, results, color=_color()))
    if changed:
        print(f"\nInputs changed during the run: {', '.join(changed)}. Rerun.")
    if args.json:
        runs = [
            run_json(
                gate.name,
                contracts[gate.scope].environment,
                [r for r in results.values() if r.node.owner == gate.name],
            )
            for gate in gates
        ]
        write_json(
            args.json,
            report_json(
                command="check",
                exit_code=exit_code,
                started=started,
                finished=_now(),
                inputs=plan.inputs,
                runs=runs,
                steps=open_steps(results),
                inputs_changed=changed,
            ),
        )
    if args.junit:
        Path(args.junit).write_text(to_junit(title, results))
    return exit_code


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        gates_dir, contracts_dir = _defaults(args)
        gates = load_directory(gates_dir)
        repository, environments = _load_contracts(contracts_dir)
        _validate_all(gates, repository, environments)
    except INVALID as exc:
        return _report_invalid(exc)
    count = len(environments) + (1 if repository else 0)
    print(f"preflight validate: {len(gates)} gates, {count} contracts: valid")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    started = _now()
    try:
        gates_dir, contracts_dir = _defaults(args)
        all_gates = load_directory(gates_dir)
        repository, environments = _load_contracts(contracts_dir)
        _validate_all(all_gates, repository, environments)
        milestones = [g for g in all_gates if g.milestone]
        repository_gates = [g for g in milestones if g.scope == "repository"]
        environment_gates = [g for g in milestones if g.scope == "environment"]
        runs = []
        if repository_gates and repository is not None:
            runs.append(("repository", repository_gates, {"repository": repository}))
        for contract in environments:
            if environment_gates:
                contracts = {"environment": contract}
                if repository is not None:
                    contracts["repository"] = repository
                runs.append((contract.environment or "", environment_gates, contracts))
        plans = [
            (label, gates, build_plan(gates, contracts, link_gates=False, strict=False))
            for label, gates, contracts in runs
        ]
    except INVALID as exc:
        return _report_invalid(exc)

    repository_names = {g.name for g in repository_gates}
    satisfied: dict[tuple[str, str], bool] = {}
    sections, main_steps, also_steps, json_runs = [], [], [], []
    changed: set[str] = set()
    inputs: dict[str, str] = {}
    for label, gates, plan in plans:
        results = run_plan(plan, jobs=args.jobs)
        rows, main_nodes, also_nodes = [], set(), set()
        for gate in gates:
            nodes = plan.nodes_of(gate.name)
            own_ok = all(results[n.id].status is Status.OK for n in nodes)
            waits = [
                r
                for r in gate.requires
                if not satisfied.get(("repository" if r in repository_names else label, r), True)
            ]
            satisfied[(label, gate.name)] = own_ok and not waits
            if waits:
                state = "waiting"
                also_nodes.update(n.id for n in nodes)
            elif own_ok:
                state = "satisfied"
            else:
                count = len(open_steps({n.id: results[n.id] for n in nodes}))
                state = f"open ({count})"
                main_nodes.update(n.id for n in nodes)
            rows.append((state, gate.name, waits))
            json_runs.append(
                run_json(
                    gate.name,
                    plan.contracts[gate.scope].environment,
                    [results[n.id] for n in nodes if n.owner == gate.name],
                )
            )
        also_nodes -= main_nodes

        def prefixed(node_ids: set[str], results=results, label=label):
            steps = open_steps({k: v for k, v in results.items() if k in node_ids})
            return [replace(step, item_id=f"{label}: {step.item_id}") for step in steps]

        main_steps += prefixed(main_nodes)
        also_steps += prefixed(also_nodes)
        sections.append((label, rows))
        for contract in plan.contracts.values():
            changed.update(contract.changed_inputs())
        inputs.update(plan.inputs)

    exit_code = 0 if all(satisfied.values()) and not changed else 1
    sys.stdout.write(render_status(sections, main_steps, also_steps))
    if changed:
        print(f"\nInputs changed during the run: {', '.join(sorted(changed))}. Rerun.")
    if args.json:
        write_json(
            args.json,
            report_json(
                command="status",
                exit_code=exit_code,
                started=started,
                finished=_now(),
                inputs=inputs,
                runs=json_runs,
                steps=main_steps + also_steps,
                inputs_changed=sorted(changed),
            ),
        )
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preflight", description="Operator guardrails that name the next step."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="run one gate against a contract")
    check.add_argument("gate", type=Path)
    check.add_argument("--contract", type=Path, required=True)
    check.add_argument("--json", type=Path)
    check.add_argument("--junit", type=Path)
    check.add_argument("--jobs", type=_positive_int, default=4)
    check.set_defaults(handler=cmd_check)

    status = commands.add_parser("status", help="where every milestone gate stands")
    status.add_argument("--gates", type=Path)
    status.add_argument("--contracts", type=Path)
    status.add_argument("--json", type=Path)
    status.add_argument("--jobs", type=_positive_int, default=4)
    status.set_defaults(handler=cmd_status)

    validate = commands.add_parser("validate", help="load every gate and contract; observe nothing")
    validate.add_argument("--gates", type=Path)
    validate.add_argument("--contracts", type=Path)
    validate.set_defaults(handler=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("preflight: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # a bug in preflight itself
        print(f"preflight: internal error ({type(exc).__name__})", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
