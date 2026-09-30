"""Where bound checks run: each in its own worker (a run), nowhere (--validate), or from a
table of outcomes (tests). `probe_now` runs one check with the current run's executor."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol

from preflight.check import BoundCheck
from preflight.identity import worker_environment
from preflight.outcome import Outcome, ok, outcome
from preflight.worker import Job, kill_all, launch

NOT_OBSERVED = "not observed (--validate)"
"""What every check observes under --validate: unknown, never present or absent. A `str`, so a
check taking a `str` argument still validates when a provider passes it on."""


class Executor(Protocol):
    def run(self, checks: Sequence[BoundCheck]) -> list[Outcome]: ...


class WorkerExecutor:
    """Each check in its own worker process, at most `jobs` at once."""

    def __init__(self, *, root: Path, gate_dir: Path, jobs: int = 4):
        self.root = root
        self.gate_dir = gate_dir
        self.jobs = max(1, jobs)

    def job(self, bound: BoundCheck) -> Job:
        check = bound.check
        return Job(
            label=bound.label,
            module=check.module,
            file=check.file,
            name=check.name,
            gate_dir=str(self.gate_dir),
            root=str(self.root),
            arguments=bound.arguments_json(),
        )

    def _one(self, bound: BoundCheck) -> Outcome:
        env = worker_environment(
            os.environ,
            bound.identity,
            keep_aws=bound.check.ambient,
            bin_dir=str(Path(sys.executable).parent),
        )
        return launch(self.job(bound), env=env, timeout=bound.timeout)

    def run(self, checks: Sequence[BoundCheck]) -> list[Outcome]:
        pool = ThreadPoolExecutor(max_workers=self.jobs)
        try:
            return list(pool.map(self._one, checks))
        except BaseException:
            kill_all()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


class StandInExecutor:
    """For --validate: nothing is observed and no worker starts; every check stands in as ok."""

    def run(self, checks: Sequence[BoundCheck]) -> list[Outcome]:
        return [outcome(ok(observed=NOT_OBSERVED)) for _ in checks]


_CURRENT: ContextVar[Executor | None] = ContextVar("preflight_executor", default=None)


@contextlib.contextmanager
def using(executor: Executor) -> Iterator[None]:
    token = _CURRENT.set(executor)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def probe_now(bound: BoundCheck) -> Outcome:
    """Runs one check now, in a worker during a run: for providers that must observe."""
    executor = _CURRENT.get()
    if executor is None:
        raise RuntimeError("probe_now runs only while a gate runs")
    return executor.run([bound])[0]
