"""The worklist: which guards passed, where the run stopped, and what to do next."""

from __future__ import annotations

from dataclasses import dataclass

from preflight.outcome import Item, NextStep, Outcome, Status

LABELS = {Status.FAIL: "FAIL", Status.ERROR: "ERROR", Status.PENDING: "pending", Status.OK: "ok"}
NO_NEXT_STEP = NextStep("No next step was recorded; rerun, and report it if it persists.")
DETAIL = " " * 6
STEP = " " * 14


@dataclass(frozen=True)
class CheckResult:
    label: str
    outcome: Outcome


@dataclass(frozen=True)
class GuardResult:
    name: str
    checks: tuple[CheckResult, ...] = ()
    unmet: tuple[Item, ...] = ()
    unmet_by: str | None = None

    @property
    def passed(self) -> bool:
        return not self.unmet and all(c.outcome.status is Status.OK for c in self.checks)

    def labelled_items(self) -> list[tuple[str, Item]]:
        pairs = [(f"needs {self.unmet_by}", item) for item in self.unmet]
        for result in self.checks:
            pairs.extend((result.label, item) for item in result.outcome.items)
        return [
            (label if item.key is None else f"{label}:{item.key}", item) for label, item in pairs
        ]


@dataclass(frozen=True)
class RunResult:
    gate: str
    arguments: tuple[str, ...] = ()
    exit_code: int = 0
    guards: tuple[GuardResult, ...] = ()
    not_run: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    validated: bool = False
    output: str = ""

    @property
    def stopped_at(self) -> str | None:
        return next((guard.name for guard in self.guards if not guard.passed), None)


def _step_lines(step: NextStep) -> list[str]:
    lines = [STEP + line for line in step.do.splitlines()]
    if step.paste:
        lines.extend(STEP + "  " + line for line in step.paste.splitlines())
    if step.wait:
        lines.append(STEP + "wait: " + step.wait)
    if step.ref:
        lines.append(STEP + "see: " + step.ref)
    return lines


def _open(guard: GuardResult) -> list[str]:
    groups: dict[tuple[Status, NextStep], list[str]] = {}
    for label, item in guard.labelled_items():
        if item.status is Status.OK or item.advisory:
            continue
        groups.setdefault((item.status, item.next_step or NO_NEXT_STEP), []).append(label)
    lines: list[str] = []
    for (status, step), labels in groups.items():
        lines.append(f"{DETAIL}{LABELS[status]:<7} {', '.join(labels)}")
        lines.extend(_step_lines(step))
    lines.extend(
        f"{DETAIL}{'ok':<7} {label}"
        for label, item in guard.labelled_items()
        if item.status is Status.OK and not item.advisory
    )
    return lines


def _warnings(result: RunResult) -> list[str]:
    return [
        f"  ! {label}  {(item.next_step or NO_NEXT_STEP).do}"
        for guard in result.guards
        for label, item in guard.labelled_items()
        if item.advisory and item.status is not Status.OK
    ]


def worklist(result: RunResult) -> str:
    lines = [" ".join(["preflight", result.gate, *result.arguments]), ""]
    for guard in result.guards:
        lines.append(f"  {'✓' if guard.passed else '✗'} {guard.name}")
        if not guard.passed:
            lines.extend(_open(guard))
    warnings = _warnings(result)
    if warnings:
        lines += ["", "Warnings:", *warnings]
    lines.append("")
    if result.problems:
        lines += ["Problems:", *(f"  - {problem}" for problem in result.problems)]
    if result.stopped_at:
        lines.append(f"Stopped at: {result.stopped_at}")
    if result.not_run:
        lines.append("Not run: " + ", ".join(result.not_run))
    if result.exit_code == 0:
        lines.append(
            "Validated every guard; nothing was observed."
            if result.validated
            else "Every guard passed."
        )
    return "\n".join(lines) + "\n"
