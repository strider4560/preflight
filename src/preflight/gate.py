"""Gates: guards run in declaration order, each a function returning checks. `run()` parses the
command line, resolves dependencies, runs each guard's checks in workers, and stops at the first
guard that does not pass."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from preflight.check import BoundCheck, CheckCallError
from preflight.params import (
    GateDefinitionError,
    Propagate,
    ProviderFailed,
    Resolver,
    Unmet,
    collect_args,
    parse_args,
)
from preflight.render import CheckResult, GuardResult, RunResult, worklist
from preflight.runner import Executor, StandInExecutor, WorkerExecutor, using
from preflight.worker import kill_all, reset_stop

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Guard:
    name: str
    fn: Callable[..., Any]


class Guards:
    """Guards in declaration order; a gate includes them where `include` is called."""

    def __init__(self) -> None:
        self.entries: list[Guard | Guards] = []

    def guard(self, name: str) -> Callable[[F], F]:
        def decorate(fn: F) -> F:
            self.entries.append(Guard(name, fn))
            return fn

        return decorate

    def include(self, guards: Guards) -> None:
        self.entries.append(guards)

    def flatten(self, trail: tuple[Guards, ...] = ()) -> list[Guard]:
        if self in trail:
            raise GateDefinitionError(["guards include each other in a cycle"])
        found: list[Guard] = []
        for entry in self.entries:
            if isinstance(entry, Guard):
                found.append(entry)
            elif isinstance(entry, Guards) and not isinstance(entry, Gate):
                found.extend(entry.flatten((*trail, self)))
            else:
                raise GateDefinitionError([f"include takes a Guards(), not {type(entry).__name__}"])
        return found


class Gate(Guards):
    def __init__(self, name: str, *, jobs: int = 4) -> None:
        super().__init__()
        self.name = name
        self.jobs = jobs
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        caller = sys._getframe(1).f_globals.get("__file__")
        self.file: Path | None = Path(caller).resolve() if caller else None

    def run(self, argv: Sequence[str] | None = None) -> NoReturn:
        def progress(name: str) -> None:
            print(f"… {name}", file=sys.stderr, flush=True)

        try:
            result = self.execute(sys.argv[1:] if argv is None else argv, progress=progress)
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        sys.stdout.write(result.output)
        sys.stdout.flush()
        raise SystemExit(result.exit_code)

    def execute(
        self,
        argv: Sequence[str],
        *,
        executor: Executor | None = None,
        root: Path | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> RunResult:
        base = RunResult(self.name, tuple(argv), exit_code=2)
        try:
            return self._execute(base, executor, root, progress)
        except Propagate:
            raise
        except Exception as exc:
            problem = f"preflight failed ({type(exc).__name__})"
            return _finish(replace(base, exit_code=3, problems=(problem,)))

    def _execute(
        self,
        base: RunResult,
        executor: Executor | None,
        root: Path | None,
        progress: Callable[[str], None] | None,
    ) -> RunResult:
        try:
            guards = self._guards()
            specs = collect_args([g.fn for g in guards], self.dependency_overrides)
        except GateDefinitionError as exc:
            return _finish(replace(base, problems=tuple(exc.problems)))
        try:
            values, validate = parse_args(self._prog(), specs, base.arguments)
        except SystemExit as exc:  # argparse printed usage (or help)
            return replace(base, exit_code=exc.code if isinstance(exc.code, int) else 2)
        try:
            root = root or self._root()
        except GateDefinitionError as exc:
            return _finish(replace(base, problems=tuple(exc.problems)))
        if validate:
            executor = StandInExecutor()
        elif executor is None:
            gate_dir = self.file.parent if self.file else root
            executor = WorkerExecutor(root=root, gate_dir=gate_dir, jobs=self.jobs)
        base = replace(base, validated=validate)
        return _finish(self._run(base, guards, values, executor, progress))

    def _run(
        self,
        base: RunResult,
        guards: list[Guard],
        values: dict[str, Any],
        executor: Executor,
        progress: Callable[[str], None] | None,
    ) -> RunResult:
        results: list[GuardResult] = []
        problems: list[str] = []
        exit_code = 0
        attempted = 0
        reset_stop()
        stack = contextlib.ExitStack()
        resolver = Resolver(values, self.dependency_overrides, stack)
        try:
            with using(executor):
                for guard in guards:
                    attempted += 1
                    if progress:
                        progress(guard.name)
                    result, problem = self._guard(guard, resolver, executor)
                    if problem is not None:
                        problems.append(problem)
                        exit_code = 2
                        break
                    results.append(result)
                    if not result.passed:
                        exit_code = 1
                        break
        except KeyboardInterrupt:
            kill_all()
            exit_code = 130
            problems.append("interrupted")
        except Propagate:
            raise
        except Exception as exc:
            kill_all()
            exit_code = 3
            problems.append(f"preflight failed ({type(exc).__name__})")
        finally:
            stack.close()
        problems.extend(resolver.cleanup_problems)
        if resolver.cleanup_problems and exit_code == 0:
            exit_code = 3
        return replace(
            base,
            exit_code=exit_code,
            guards=tuple(results),
            not_run=tuple(g.name for g in guards[attempted:]),
            problems=tuple(problems),
        )

    def _guard(
        self, guard: Guard, resolver: Resolver, executor: Executor
    ) -> tuple[GuardResult | None, str | None]:
        try:
            kwargs = resolver.arguments(guard.fn)
        except Unmet as exc:
            return GuardResult(guard.name, unmet=exc.items, unmet_by=exc.provider), None
        except ProviderFailed as exc:
            return None, f"guard {guard.name!r}: {exc}"
        try:
            checks = guard.fn(**kwargs)
        except CheckCallError as exc:
            return None, f"guard {guard.name!r}: {exc}"
        except (Exception, SystemExit) as exc:
            return None, f"guard {guard.name!r} raised {type(exc).__name__}"
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(c, BoundCheck) for c in checks)
        ):
            return None, f"guard {guard.name!r} must return a non-empty list of checks"
        outcomes = executor.run(checks)
        pairs = zip(_labels(checks), outcomes, strict=True)
        return GuardResult(guard.name, tuple(CheckResult(label, o) for label, o in pairs)), None

    def _guards(self) -> list[Guard]:
        guards = self.flatten()
        if not guards:
            raise GateDefinitionError([f"gate {self.name} has no guards"])
        repeated = sorted(n for n, c in Counter(g.name for g in guards).items() if c > 1)
        if repeated:
            raise GateDefinitionError([f"guard name used twice: {n}" for n in repeated])
        return guards

    def _prog(self) -> str:
        return self.file.name if self.file else self.name

    def _root(self) -> Path:
        if self.file is None:
            raise GateDefinitionError(["the gate's file is unknown; define the gate in a file"])
        try:
            done = subprocess.run(
                ["git", "-C", str(self.file.parent), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
            )
        except OSError:
            raise GateDefinitionError(["git is not available"]) from None
        if done.returncode != 0:
            raise GateDefinitionError([f"{self.file.name} is not inside a git work tree"])
        return Path(done.stdout.strip()).resolve()


def _labels(checks: Sequence[BoundCheck]) -> list[str]:
    counts = Counter(c.label for c in checks)
    seen: Counter[str] = Counter()
    labels = []
    for bound in checks:
        if counts[bound.label] > 1:
            seen[bound.label] += 1
            labels.append(f"{bound.label} #{seen[bound.label]}")
        else:
            labels.append(bound.label)
    return labels


def _finish(result: RunResult) -> RunResult:
    return replace(result, output=worklist(result))
