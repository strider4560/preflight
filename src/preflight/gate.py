"""Gates: guards run in declaration order, each a function returning checks. `run()` parses the
command line, resolves dependencies, runs each guard's checks in workers, and stops at the first
guard that does not pass."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
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
        try:
            with _terminate_as_interrupt():
                result = self.execute(sys.argv[1:] if argv is None else argv, progress=_progress)
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        _emit(result)
        raise SystemExit(result.exit_code)

    @contextlib.contextmanager
    def checked(
        self,
        argv: Sequence[str] | None = None,
        *,
        executor: Executor | None = None,
        root: Path | None = None,
    ) -> Iterator[Run]:
        """Runs the guards and enters the block only when every one passed, with the providers
        still alive; otherwise prints the worklist and exits as run() would. Leaving the block
        runs the providers' cleanup, however the block ends."""
        arguments = tuple(sys.argv[1:] if argv is None else argv)
        base = RunResult(self.name, arguments, exit_code=2)
        try:
            with _terminate_as_interrupt():
                yield from self._checked(base, executor, root)
        except KeyboardInterrupt:  # before the guards, or during the providers' cleanup
            raise SystemExit(130) from None

    def _checked(
        self, base: RunResult, executor: Executor | None, root: Path | None
    ) -> Iterator[Run]:
        try:
            prepared = self._prepare(base, executor, root)
        except Propagate:
            raise
        except Exception as exc:
            problem = f"preflight failed ({type(exc).__name__})"
            prepared = _finish(replace(base, exit_code=3, problems=(problem,)))
        if isinstance(prepared, RunResult):
            _emit(prepared)
            raise SystemExit(prepared.exit_code)
        session = _Session(self, prepared.values, prepared.executor)
        session.open()
        code = 0
        passing: SystemExit | None = None  # held until cleanup, which may turn it into 3
        try:
            result = _finish(session.run_guards(prepared.base, prepared.guards, _progress))
            _emit(result)
            if result.exit_code != 0 or result.validated:
                raise SystemExit(result.exit_code)
            yield Run(self, session, prepared)
        except KeyboardInterrupt:
            kill_all()
            code = 130
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
            passing = exc
        except Propagate:
            raise
        except Unmet as exc:  # the block's own observed(), as run[provider] reports one
            _emit(_unmet(prepared.base, exc, exc.provider or "the program"))
            code = 1
        except Exception as exc:
            kill_all()
            code = 2
            self._report_failure(exc)
        finally:
            session.close()
            for problem in session.resolver.cleanup_problems:
                print(f"{self._prog()}: {problem}", file=sys.stderr)
        if session.resolver.cleanup_problems and code == 0:
            code = 3
        if code:
            raise SystemExit(code)
        if passing is not None:
            raise passing  # re-raised, not returned: contextlib would swallow a return

    def _report_failure(self, exc: Exception) -> None:
        """A definition error names arguments and guards, never values; anything else, its type."""
        if isinstance(exc, GateDefinitionError):
            for problem in exc.problems:
                print(f"{self._prog()}: {problem}", file=sys.stderr)
        else:
            print(f"{self._prog()}: the program raised {type(exc).__name__}", file=sys.stderr)

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
        prepared = self._prepare(base, executor, root)
        if isinstance(prepared, RunResult):
            return prepared
        session = _Session(self, prepared.values, prepared.executor)
        session.open()
        try:
            result = session.run_guards(prepared.base, prepared.guards, progress)
        finally:
            session.close()
        problems = tuple(session.resolver.cleanup_problems)
        exit_code = 3 if problems and result.exit_code == 0 else result.exit_code
        return _finish(replace(result, exit_code=exit_code, problems=result.problems + problems))

    def _prepare(
        self, base: RunResult, executor: Executor | None, root: Path | None
    ) -> _Prepared | RunResult:
        """Everything before a guard runs; a RunResult is a finished failure (exit 2)."""
        try:
            guards = self._guards()
            specs = collect_args([g.fn for g in guards], self.dependency_overrides)
        except GateDefinitionError as exc:
            return _finish(replace(base, problems=tuple(exc.problems)))
        try:
            values, validate = parse_args(self._prog(), specs, base.arguments)
        except SystemExit:  # argparse printed usage or help; neither is a pass
            return replace(base, exit_code=2)
        try:
            root = root or self._root()
        except GateDefinitionError as exc:
            return _finish(replace(base, problems=tuple(exc.problems)))
        if validate:
            executor = StandInExecutor()
        elif executor is None:
            gate_dir = self.file.parent if self.file else root
            executor = WorkerExecutor(root=root, gate_dir=gate_dir, jobs=self.jobs)
        return _Prepared(replace(base, validated=validate), guards, values, executor)

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
        return _unique_guards(self, self.name)

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


@dataclass(frozen=True)
class _Prepared:
    base: RunResult
    guards: list[Guard]
    values: dict[str, Any]
    executor: Executor


class _Session:
    """One run's live state: the executor in use, the resolver and its cleanup stack. execute()
    opens and closes it around the guards; checked() keeps it open for the block."""

    def __init__(self, gate: Gate, values: dict[str, Any], executor: Executor):
        self.gate = gate
        self.executor = executor
        self.stack = contextlib.ExitStack()
        self.resolver = Resolver(values, gate.dependency_overrides, self.stack)
        self._using = using(executor)

    def open(self) -> None:
        reset_stop()
        self._using.__enter__()

    def close(self) -> None:
        try:
            self.stack.close()
        finally:
            self._using.__exit__(None, None, None)

    def run_guards(
        self, base: RunResult, guards: list[Guard], progress: Callable[[str], None] | None
    ) -> RunResult:
        results: list[GuardResult] = []
        problems: list[str] = []
        exit_code = 0
        attempted = 0
        current: str | None = None  # the guard being attempted, named if it is cut short
        try:
            for guard in guards:
                attempted += 1
                current = guard.name
                if progress:
                    progress(guard.name)
                result, problem = self.gate._guard(guard, self.resolver, self.executor)
                current = None
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
            problems.append(f"interrupted during guard {current!r}" if current else "interrupted")
        except Propagate:
            raise
        except Exception as exc:
            kill_all()
            exit_code = 3
            where = f" in guard {current!r}" if current else ""
            problems.append(f"preflight failed{where} ({type(exc).__name__})")
        return replace(
            base,
            exit_code=exit_code,
            guards=tuple(results),
            not_run=tuple(g.name for g in guards[attempted:]),
            problems=tuple(problems),
        )


class Run:
    """The scope of a passed gate: its arguments, its providers, and verification afterwards."""

    def __init__(self, gate: Gate, session: _Session, prepared: _Prepared):
        self._gate = gate
        self._session = session
        self._prepared = prepared
        self.args: Mapping[str, Any] = MappingProxyType(dict(prepared.values))

    def __getitem__(self, provider: Callable[..., Any]) -> Any:
        try:
            return self._session.resolver.provide(provider)
        except Unmet as exc:
            name = exc.provider or getattr(provider, "__name__", "provider")
            _emit(_unmet(self._prepared.base, exc, name))
            raise SystemExit(1) from None
        except ProviderFailed as exc:
            print(f"{self._gate._prog()}: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    def verify(self, guards: Guards) -> None:
        """Runs more guards in this scope; exits when one stops, returns when every one passed."""
        flat = _unique_guards(guards, self._gate.name)
        specs = collect_args([g.fn for g in flat], self._gate.dependency_overrides)
        unknown = [spec.name for spec in specs if spec.name not in self.args]
        if unknown:
            raise GateDefinitionError(
                [f"verify: argument {name} is not an argument of the gate" for name in unknown]
            )
        result = _finish(self._session.run_guards(self._prepared.base, flat, _progress))
        _emit(result)
        if result.exit_code != 0:
            raise SystemExit(result.exit_code)


def _unique_guards(guards: Guards, name: str) -> list[Guard]:
    flat = guards.flatten()
    if not flat:
        raise GateDefinitionError([f"gate {name} has no guards"])
    repeated = sorted(n for n, c in Counter(g.name for g in flat).items() if c > 1)
    if repeated:
        raise GateDefinitionError([f"guard name used twice: {n}" for n in repeated])
    return flat


def _progress(name: str) -> None:
    print(f"… {name}", file=sys.stderr, flush=True)


def _emit(result: RunResult) -> None:
    sys.stdout.write(result.output)
    sys.stdout.flush()


@contextlib.contextmanager
def _terminate_as_interrupt() -> Iterator[None]:
    """SIGTERM and SIGHUP (a cancelled CI job, a closed terminal) interrupt the run as Ctrl-C
    does, so workers are killed and providers clean up; the previous handlers are restored."""

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous: dict[int, Any] = {}
    try:
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGTERM", "SIGHUP"):
                number = getattr(signal, name, None)
                if number is not None:
                    previous[number] = signal.signal(number, interrupt)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)


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


def _unmet(base: RunResult, exc: Unmet, name: str) -> RunResult:
    """A worklist of one guard, `needs <name>`, carrying the unmet steps (exit 1)."""
    guard = GuardResult(f"needs {name}", unmet=exc.items, unmet_by=name)
    return _finish(replace(base, exit_code=1, guards=(guard,)))


def _finish(result: RunResult) -> RunResult:
    return replace(result, output=worklist(result))
