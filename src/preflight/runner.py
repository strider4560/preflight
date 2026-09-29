"""Runs a plan: instances in dependency order, at most `jobs` at once. An instance whose
prerequisite is not ok is blocked and never run."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from preflight.check import REGISTRY
from preflight.graph import Node, Plan
from preflight.identity import Identity, worker_environment
from preflight.outcome import Outcome, Status, apply_remedy, error, outcome
from preflight.worker import Job, encode_data, kill_all, launch, reset_stop


@dataclass
class NodeResult:
    node: Node
    status: Status
    outcome: Outcome | None
    blocked_by: list[str]
    duration: float


Launcher = Callable[[Node], Outcome]


def _identity(node: Node) -> Identity | None:
    if node.identity is None:
        return None
    identity = node.contract.identity(node.identity)
    region = node.data.get("region")
    if isinstance(region, str):
        identity = identity.model_copy(update={"region": region})
    return identity


def job_for(node: Node) -> Job:
    check = REGISTRY[node.check_id]
    identity = _identity(node)
    consumer = sys.modules.get("consumer")
    consumer_dir = (
        consumer.__path__[0]
        if consumer is not None and check.module.startswith("consumer.")
        else None
    )
    return Job(
        node_id=node.id,
        check_id=node.check_id,
        module=check.module,
        consumer_dir=consumer_dir,
        root=str(node.contract.root),
        environment=node.contract.environment,
        data=encode_data(node.data),
        identity=identity.model_dump() if identity else None,
        identities={a: i.model_dump() for a, i in node.contract.ready_identities().items()},
    )


def default_launcher(node: Node) -> Outcome:
    try:
        check = REGISTRY[node.check_id]
        env = worker_environment(
            os.environ,
            _identity(node),
            keep_aws=check.ambient,
            bin_dir=str(Path(sys.executable).parent),
        )
        job = job_for(node)
    except Exception as exc:
        name = type(exc).__name__
        return outcome(
            error(do=f"Preflight could not prepare {node.check_id} ({name}).", error_type=name)
        )
    return launch(job, env=env, timeout=node.timeout)


def _timed(launcher: Launcher, node: Node) -> tuple[Outcome, float]:
    start = time.monotonic()
    result = launcher(node)
    return result, time.monotonic() - start


def _finish(node: Node, result: Outcome, duration: float) -> NodeResult:
    merged = apply_remedy(result, node.remedy(), node.template_values())
    return NodeResult(node, merged.status, merged, [], duration)


def run_plan(
    plan: Plan, *, jobs: int = 4, launcher: Launcher = default_launcher
) -> dict[str, NodeResult]:
    reset_stop()
    order = plan.ordered()
    results: dict[str, NodeResult] = {}
    waiting = list(order)
    running: dict[Future, Node] = {}
    jobs = max(1, jobs)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        try:
            while waiting or running:
                progressed = False
                for node in list(waiting):
                    if any(dep not in results for dep in node.requires):
                        continue
                    not_ok = [d for d in node.requires if results[d].status is not Status.OK]
                    if not_ok:
                        results[node.id] = NodeResult(node, Status.BLOCKED, None, not_ok, 0.0)
                    elif node.static is not None:
                        results[node.id] = _finish(node, node.static, 0.0)
                    elif len(running) < jobs:
                        running[pool.submit(_timed, launcher, node)] = node
                    else:
                        continue
                    waiting.remove(node)
                    progressed = True
                if running:
                    done, _ = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        node = running.pop(future)
                        result, duration = future.result()
                        results[node.id] = _finish(node, result, duration)
                elif not progressed:
                    raise RuntimeError("some instances wait on prerequisites that never finish")
        except BaseException:
            kill_all()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return {node.id: results[node.id] for node in order}
