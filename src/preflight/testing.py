"""Testing a gate in process: stand-in outcomes by check, providers replaced through
`gate.dependency_overrides`, and nothing observed."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from preflight.check import BoundCheck, Check
from preflight.gate import Gate
from preflight.outcome import Outcome
from preflight.params import Propagate
from preflight.render import RunResult

Answer = Outcome | Callable[[BoundCheck], Outcome]


class MissingStandIn(Propagate):
    """The test gave no stand-in outcome for a check the gate ran."""


class StandIns:
    def __init__(self, outcomes: Mapping[Check, Answer]):
        self.outcomes = dict(outcomes)

    def run(self, checks: Sequence[BoundCheck]) -> list[Outcome]:
        results = []
        for bound in checks:
            answer = self.outcomes.get(bound.check)
            if answer is None:
                raise MissingStandIn(f"no stand-in outcome for {bound.label}")
            results.append(answer if isinstance(answer, Outcome) else answer(bound))
        return results


class GateClient:
    def __init__(
        self,
        gate: Gate,
        outcomes: Mapping[Check, Answer] | None = None,
        *,
        root: Path | None = None,
    ):
        self.gate = gate
        self.outcomes = dict(outcomes or {})
        self.root = root

    def run(self, argv: Sequence[str] = ()) -> RunResult:
        return self.gate.execute(
            list(argv), executor=StandIns(self.outcomes), root=self.root or Path.cwd()
        )
