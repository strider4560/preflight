"""The worklist in the terminal, and the same results as JSON and JUnit."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import preflight
from preflight.outcome import Item, NextStep, Status
from preflight.runner import NodeResult

LABELS = {
    Status.OK: "ok",
    Status.FAIL: "FAIL",
    Status.PENDING: "pending",
    Status.ERROR: "ERROR",
    Status.BLOCKED: "blocked",
}
COLORS = {
    Status.OK: "32",
    Status.FAIL: "31",
    Status.PENDING: "33",
    Status.ERROR: "31",
    Status.BLOCKED: "90",
}
WARN_COLOR = "33"


@dataclass(frozen=True)
class OpenStep:
    item_id: str
    node_id: str
    status: Status
    next_step: NextStep
    also: tuple[str, ...] = ()
    unblocks: tuple[str, ...] = ()


def item_id(node_id: str, item: Item) -> str:
    return node_id if item.key is None else f"{node_id}:{item.key}"


def open_steps(results: Mapping[str, NodeResult]) -> list[OpenStep]:
    unblocks: dict[str, list[str]] = {}
    for result in results.values():
        for dependency in result.blocked_by:
            unblocks.setdefault(dependency, []).append(result.node.id)
    urgent: list[OpenStep] = []
    settling: list[OpenStep] = []
    for result in results.values():
        if result.outcome is None:
            continue
        for item in result.outcome.items:
            if item.advisory or item.status is Status.OK or item.next_step is None:
                continue
            step = OpenStep(
                item_id(result.node.id, item),
                result.node.id,
                item.status,
                item.next_step,
                (),
                tuple(unblocks.get(result.node.id, ())),
            )
            (settling if item.status is Status.PENDING else urgent).append(step)
    merged: list[OpenStep] = []
    for step in urgent + settling:
        key = (step.next_step.do, step.next_step.paste)
        for index, existing in enumerate(merged):
            if (existing.next_step.do, existing.next_step.paste) == key:
                extra = tuple(u for u in step.unblocks if u not in existing.unblocks)
                merged[index] = replace(
                    existing,
                    also=existing.also + (step.item_id,),
                    unblocks=existing.unblocks + extra,
                )
                break
        else:
            merged.append(step)
    return merged


def warnings(results: Mapping[str, NodeResult]) -> list[tuple[str, NextStep | None]]:
    return [
        (item_id(result.node.id, item), item.next_step)
        for result in results.values()
        if result.outcome is not None
        for item in result.outcome.items
        if item.advisory and item.status is not Status.OK
    ]


def _label(status: Status, *, advisory: bool, color: bool) -> str:
    padded = f"{'warn' if advisory else LABELS[status]:<8}"
    if not color:
        return padded
    return f"\033[{WARN_COLOR if advisory else COLORS[status]}m{padded}\033[0m"


def render_results(results: Mapping[str, NodeResult], *, color: bool) -> list[str]:
    lines = []
    for result in results.values():
        if result.outcome is None:
            waits = ", ".join(result.blocked_by)
            label = _label(Status.BLOCKED, advisory=False, color=color)
            lines.append(f"  {label} {result.node.id}  (waits on: {waits})")
            continue
        for item in result.outcome.items:
            advisory = item.advisory and item.status is not Status.OK
            label = _label(item.status, advisory=advisory, color=color)
            lines.append(f"  {label} {item_id(result.node.id, item)}")
    return lines


def render_steps(steps: Sequence[OpenStep], *, first_is_next: bool = True) -> list[str]:
    lines = []
    for number, step in enumerate(steps, 1):
        marker = "> NEXT" if first_is_next and number == 1 else f"  {number}."
        also = f" (also {', '.join(step.also)})" if step.also else ""
        lines.append(f"{marker:<7} {step.item_id}{also}")
        lines.append(f"        {step.next_step.do}")
        if step.next_step.paste:
            lines.extend(f"          {line}" for line in step.next_step.paste.splitlines())
        if step.next_step.wait:
            lines.append(f"        wait: {step.next_step.wait}")
        if step.next_step.ref:
            lines.append(f"        see: {step.next_step.ref}")
        if step.unblocks:
            lines.append(f"        then unblocks: {', '.join(step.unblocks)}")
    return lines


def render_check(title: str, results: Mapping[str, NodeResult], *, color: bool) -> str:
    steps = open_steps(results)
    lines = [title, "", *render_results(results, color=color), ""]
    lines += ["Open steps:", *render_steps(steps)] if steps else ["Nothing open."]
    warned = warnings(results)
    if warned:
        lines += ["", "Warnings:"]
        lines += [f"  {ident}: {step.do if step else 'see the check'}" for ident, step in warned]
    return "\n".join(lines) + "\n"


def _item_json(node_id: str, item: Item) -> dict[str, Any]:
    step = item.next_step
    return {
        "id": item_id(node_id, item),
        "status": item.status.value,
        "advisory": item.advisory,
        "observed": item.observed,
        "next_step": (
            {"do": step.do, "paste": step.paste, "wait": step.wait, "ref": step.ref}
            if step
            else None
        ),
    }


def instance_json(result: NodeResult) -> dict[str, Any]:
    node = result.node
    items = result.outcome.items if result.outcome else ()
    return {
        "id": node.id,
        "check": node.check_id,
        "section": node.key if node.section_bound else None,
        "gate": node.owner,
        "status": result.status.value,
        "requires": list(node.requires),
        "blocked_by": list(result.blocked_by),
        "duration_s": round(result.duration, 3),
        "error_type": next((i.error_type for i in items if i.error_type), None),
        "items": [_item_json(node.id, item) for item in items],
    }


def run_json(gate: str, environment: str | None, results: Sequence[NodeResult]) -> dict[str, Any]:
    return {
        "gate": gate,
        "environment": environment,
        "instances": [instance_json(r) for r in results],
    }


def report_json(
    *,
    command: str,
    exit_code: int,
    started: str,
    finished: str,
    inputs: Mapping[str, str],
    runs: list[dict[str, Any]],
    steps: Sequence[OpenStep],
    inputs_changed: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "preflight_version": preflight.__version__,
        "command": command,
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": finished,
        "inputs": [{"path": p, "sha256": h} for p, h in sorted(inputs.items())],
        "inputs_changed": inputs_changed,
        "runs": runs,
        "open": [step.item_id for step in steps],
        "next": steps[0].item_id if steps else None,
    }


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(data, handle, indent=2, default=str)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def to_junit(name: str, results: Mapping[str, NodeResult]) -> str:
    suite = ElementTree.Element("testsuite", name=name)
    tests = failures = errors = 0
    for result in results.values():
        entries: list[tuple[str, Item | None]] = (
            [(result.node.id, None)]
            if result.outcome is None
            else [(item_id(result.node.id, i), i) for i in result.outcome.items]
        )
        for ident, item in entries:
            tests += 1
            case = ElementTree.SubElement(
                suite, "testcase", classname=result.node.check_id, name=ident
            )
            if item is None:
                failures += 1
                ElementTree.SubElement(
                    case,
                    "failure",
                    type="blocked",
                    message=f"waits on {', '.join(result.blocked_by)}",
                )
                continue
            message = item.next_step.do if item.next_step else item.status.value
            if item.status is Status.OK:
                continue
            if item.advisory:
                ElementTree.SubElement(case, "system-out").text = f"warning: {message}"
            elif item.status is Status.ERROR:
                errors += 1
                ElementTree.SubElement(
                    case, "error", type=item.error_type or "error", message=message
                )
            else:
                failures += 1
                ElementTree.SubElement(case, "failure", type=item.status.value, message=message)
    suite.set("tests", str(tests))
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    return ElementTree.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"
