# Preflight gates as programs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn preflight from a contract-driven CLI into a FastAPI-style assertion library: a gate is an executable Python script whose decorated guards return checks, run in order in isolated workers, stopping at the first guard that does not pass.

**Architecture:** Checks become functions whose calls validate their arguments with pydantic and return bound checks; each bound check still runs in its own worker process with a cleaned environment and a process-group timeout. A `Gate` holds guards (decorated functions returning checks), resolves their `Arg` (command line) and `Depends` (provider) parameters once per run, runs each guard's checks through an executor, prints a worklist and exits. The contract layer, reference resolvers, remedy tables, the dependency graph and the `preflight` command are removed.

**Tech Stack:** Python ≥3.12, pydantic 2, pytest-testinfra (local and Ansible backends), Ansible `amazon.aws`, dnspython, PyYAML, `uv` for running gate scripts (PEP 723), pytest and ruff for development.

**Spec:** `docs/superpowers/specs/2026-09-29-preflight-gate-programs-design.md`. Read it before any task.

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, lint `select = ["E", "F", "I", "B", "UP"]`.
- Development commands (from the repo root): `uv run pytest`; `uv run ruff check src tests && uv run ruff format --check src tests`. A task is done only when both pass.
- Runtime dependencies after Task 1: `ansible>=11`, `boto3>=1.40,<2`, `dnspython>=2.7,<3`, `pydantic>=2.11,<3`, `pytest-testinfra>=10,<11`, `PyYAML>=6,<7`. `python-hcl2` is removed; nothing may add it back to preflight.
- Exit codes of a gate run: `0` every guard passed; `1` a guard stopped the run; `2` the gate is wrong (bad command line, wrong check arguments, bad guard return, duplicate guard name or `Arg` conflict, an exception from the gate's own code); `3` preflight itself failed (including a cleanup that raised after a passing run); `130` interrupted.
- An exception from a check, a guard or a provider is reported by where it happened and its type only (`provider admin raised KeyError`); its message never reaches output, items or reports.
- Probes go through testinfra and Ansible modules (`probe.host`, `probe.ansible_host`, `probe.aws_module`, `probe.ssm_lookup`); direct libraries only where no module can observe the fact (DNS). Do not replace testinfra.
- Every check runs in its own worker process started as `python -P -m preflight.worker`, in a new session, with the environment cleaned by `preflight.identity.worker_environment` (unchanged). Timeouts kill the whole process group.
- Preflight never runs the command it gates and never changes what it observes.
- If ruff reports E501 on a line holding a message or expected-output string, split the literal into adjacent string literals inside parentheses without changing the text.
- Next steps are each check's own text, built from its arguments and observations. There is no `how`, no remedy table and no `generic` flag anywhere.
- Commits: Conventional Commits with a scope (`feat(core)`, `feat(catalog)`, `refactor(core)`, `test(core)`, `docs`), each ending with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
  ```
- Work on branch `feat/gate-programs` in `~/Develop/preflight`. Never push, tag or open a pull request without the operator's explicit approval.
- Subagents run on Opus 5.5 or Sonnet 5.5 only (`model: "opus"` or `"sonnet"`), always passed explicitly.

## Review Focus

1. A gate run from a directory other than the repository (as `scripts/bootstrap.sh` does, by absolute path): the repository root, the gate's own imports and the workers' imports come from the gate file's location, not the working directory. Test: Task 7, `test_a_gate_runs_from_any_directory`.
2. A check defined inside the gate script itself (module `__main__`) runs in a worker without the worker running the gate: the worker loads the file under another module name, so `if __name__ == "__main__": gate.run()` does not fire. Tests: Task 1, `test_a_check_in_a_gate_file_loads_without_running_the_gate`; Task 7 end to end.
3. Ctrl-C during a guard: every worker's process group is killed, providers' cleanup still runs, the exit is 130 and nothing is reported as passing. Test: Task 5, `test_an_interrupt_kills_workers_cleans_up_and_exits_130`.
4. An exception message raised by gate code (a provider reading tfvars, a guard) may carry a value; only the type may appear. Test: Task 5, `test_provider_and_guard_exceptions_report_only_their_type`.
5. A gate run must not leave bytecode in the consumer's repository, from the gate process or from workers. Tests: Task 1, `test_the_worker_writes_no_bytecode_into_the_gate_directory`; Task 7, `test_no_bytecode_is_left_in_the_repository`.

---

## File Structure

| Path | After this plan |
|---|---|
| `src/preflight/__init__.py` | Public surface: `Gate`, `Guards`, `Arg`, `Depends`, `Unmet`, `check`, `Check`, `BoundCheck`, `CheckCallError`, `Probe`, `probe_now`, outcome helpers. Sets `sys.dont_write_bytecode` |
| `src/preflight/outcome.py` | `Status`, `Item`, `NextStep`, `Outcome`, `ok`/`fail`/`pending`/`error`/`outcome` (no `generic`, no `BLOCKED`, no `apply_remedy`) |
| `src/preflight/check.py` | `@check`, `Check`, `BoundCheck`, `CheckCallError`, `UniqueList`, `unique_by` |
| `src/preflight/probe.py` | `Probe` (was `context.py`), Ansible host setup, `ModuleFailed` |
| `src/preflight/worker.py` | `Job`, `load_module`, `execute`, `launch`, `kill_all`, `reset_stop`, `main` |
| `src/preflight/identity.py` | Unchanged: `Identity` and `worker_environment` |
| `src/preflight/params.py` | `Arg`, `Depends`, `Unmet`, `Propagate`, `ProviderFailed`, `GateDefinitionError`, `ArgSpec`, `parameters_of`, `collect_args`, `parse_args`, `Resolver` |
| `src/preflight/runner.py` | `Executor`, `WorkerExecutor`, `StandInExecutor`, `using`, `probe_now` |
| `src/preflight/render.py` | `CheckResult`, `GuardResult`, `RunResult`, `worklist` |
| `src/preflight/gate.py` | `Guard`, `Guards`, `Gate` (`run`, `execute`) |
| `src/preflight/testing.py` | `GateClient`, `StandIns`, `MissingStandIn` |
| `src/preflight/dnsclient.py` | Unchanged |
| `src/preflight/catalog/*.py` | Checks with typed arguments; `aws.Identity` re-exported; `aws.signed_in` |
| Removed | `cli.py`, `contract.py`, `resolvers.py`, `graph.py`, old `gate.py`, old `runner.py`, old `render.py`, `context.py`, `__main__.py`; their tests; `tests/sample_checks.py` |

---

### Task 1: Checks take their inputs as arguments; contracts, the CLI and remedies go

This task replaces the section model in one step, because every catalog module, the worker and the old contract/graph/CLI layers share it. Most catalog work is mechanical; the rules are exact.

**Files:**
- Delete: `src/preflight/cli.py`, `src/preflight/contract.py`, `src/preflight/resolvers.py`, `src/preflight/graph.py`, `src/preflight/gate.py`, `src/preflight/runner.py`, `src/preflight/render.py`, `src/preflight/__main__.py`
- Delete: `tests/test_cli.py`, `tests/test_contract.py`, `tests/test_resolvers.py`, `tests/test_graph.py`, `tests/test_gate.py`, `tests/test_runner.py`, `tests/test_render.py`, `tests/test_status.py`, `tests/test_end_to_end.py`, `tests/sample_checks.py`
- Move: `src/preflight/context.py` → `src/preflight/probe.py`; `tests/test_context.py` → `tests/test_probe.py`
- Rewrite: `src/preflight/check.py`, `src/preflight/worker.py`, `src/preflight/outcome.py`, `src/preflight/__init__.py`
- Modify: every `src/preflight/catalog/*.py` except `__init__.py`
- Rewrite: `tests/test_check.py`, `tests/test_worker.py`, `tests/test_catalog_ssm.py`, `tests/test_catalog_tofu.py`
- Modify: `tests/fakes.py`, `tests/conftest.py`, `tests/test_outcome.py`, `tests/test_probe.py`, `tests/test_catalog_aws.py`, `tests/test_catalog_acm.py`, `tests/test_catalog_dns.py`, `tests/test_catalog_github.py`, `tests/test_catalog_repo.py`
- Modify: `pyproject.toml`, `uv.lock`

**Interfaces:**
- Consumes: `preflight.identity.Identity`, `preflight.identity.worker_environment` (unchanged).
- Produces:
  - `preflight.check.check` — decorator, usable bare (`@check`) or with keywords `@check(key: str | None = None, timeout: float = 60.0, ambient: bool = False)`; returns a `Check`.
  - `Check` (frozen dataclass, hashable): `id: str`, `observe: Callable[..., Outcome]` (the raw function, first parameter the probe), `arguments: type[BaseModel]`, `key: str | None`, `timeout: float`, `ambient: bool`; properties `module: str`, `name: str`, `file: str | None`; `__call__(*args, timeout: float | None = None, **kwargs) -> BoundCheck`, raising `CheckCallError`.
  - `BoundCheck` (frozen dataclass): `check: Check`, `arguments: BaseModel`, `timeout: float`; properties `values: dict[str, Any]`, `label: str`, `identity: Identity | None`; method `arguments_json() -> str`.
  - `CheckCallError(check_id: str, problems: list[str])`, `str()` is `"<id>: <problem>; <problem>"`.
  - `preflight.probe.Probe(root: Path, identity: Identity | None = None, environ: Mapping[str, str] = <copy of os.environ>, _host=None, _ansible_host=None, _dns=None)` with `host`, `ansible_host`, `dns`, `path(relative) -> Path`, `aws_module(module, args=None, *, ambient=False, expect) -> dict`, `ssm_lookup(name) -> str | None`.
  - `preflight.worker.Job(label: str, module: str, file: str | None, name: str, gate_dir: str | None, root: str, arguments: str)`, `execute(job, *, probe_factory=Probe) -> Outcome`, `launch(job, *, env, timeout, python=sys.executable) -> Outcome`, `kill_all()`, `reset_stop()`.
  - Catalog check signatures (ids unchanged unless noted):
    - `aws.session(identity: Identity)`, `aws.assumed(identity: Identity)` (`ambient=True`), `aws.region(identity: Identity)`
    - `ssm.parameters_exist(names: list[SsmName] (unique, ≥1), identity: Identity)` — new id; `ssm.present` and `ssm.parameters` are removed
    - `tofu.plan_clean(dir: str, var_files: tuple[str, ...] = (), identity: Identity | None = None)`, `key="dir"`, `timeout=600`
    - `git.up_to_date(remote: GitName = "origin", branch: GitName = "main")`
    - `files.present|absent|git_ignored|committed(paths: list[str] (unique, ≥1))`
    - `sops.rule(paths: list[str] (unique, ≥1), min_recipients: int (≥1) = 1, config: str = ".sops.yaml")`
    - `acm.issued(arn: str, identity: Identity)`, `key="arn"`
    - `dns.delegated(root: Domain, name_servers: dict[Label, list[Domain]])`, `key="root"`; `dns.undelegated(root: Domain, zones: Zones)`, `key="root"`; `dns.cname(records: Records)`; `dns.caa(domain: Domain, issuers: list[Domain] (≥1))`, `key="domain"`
    - `github.auth(hostname: str = "github.com")`; `github.repo(repo: Repo, actions_access: str | None = None)`, `key="repo"`; `github.variables(repo: Repo, variables: dict[Name, str])`, `key="repo"`; `github.org_variables(org: Owner, variables: dict[Name, str])`, `key="org"` (new id); `github.environments(repo: Repo, environments: list[EnvName] (unique, ≥1))`, `key="repo"`; `github.ruleset(repo: Repo, name: str, required_checks: tuple[str, ...] = (), enforcement: str = "active")`, `key="name"`; `github.secret_names(repo: Repo, names: list[Name] (unique, ≥1), environment: EnvName | None = None)`, `key="repo"`; `github.workflow_green(repo: Repo, workflow: str, branch: str | None = None, event: str | None = None)`, `key="workflow"`
  - `tests/fakes.py`: `make_probe(root, *, host=None, ansible=None, dns=None, identity=IDENTITY, environ=None) -> Probe` and `observe(bound: BoundCheck, probe: Probe) -> Outcome`.

- [ ] **Step 1: Delete the contract layer, the CLI and their tests**

```bash
cd ~/Develop/preflight
git rm -q src/preflight/cli.py src/preflight/contract.py src/preflight/resolvers.py \
  src/preflight/graph.py src/preflight/gate.py src/preflight/runner.py src/preflight/render.py \
  src/preflight/__main__.py
git rm -q tests/test_cli.py tests/test_contract.py tests/test_resolvers.py tests/test_graph.py \
  tests/test_gate.py tests/test_runner.py tests/test_render.py tests/test_status.py \
  tests/test_end_to_end.py tests/sample_checks.py
git mv src/preflight/context.py src/preflight/probe.py
git mv tests/test_context.py tests/test_probe.py
```

- [ ] **Step 2: Drop the console script and python-hcl2**

In `pyproject.toml`, delete the whole `[project.scripts]` table (two lines) and the line `  "python-hcl2>=8,<9",` from `dependencies`, and set the description:

```toml
description = "Operator guardrails: gate programs that stop a script and name the next step"
```

Then run: `uv lock`
Expected: `uv.lock` rewritten without `python-hcl2` (check with `grep -c python-hcl2 uv.lock` → `0`).

- [ ] **Step 3: Rewrite `src/preflight/outcome.py` without remedies**

Replace the file with:

```python
"""What one observation produced: statuses, items, and the operator's next step."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    PENDING = "pending"
    ERROR = "error"


# Worst first: an outcome takes the worst status among its blocking items.
SEVERITY = (Status.ERROR, Status.FAIL, Status.PENDING, Status.OK)


@dataclass(frozen=True)
class NextStep:
    do: str
    paste: str | None = None
    wait: str | None = None
    ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"do": self.do, "paste": self.paste, "wait": self.wait, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NextStep:
        return cls(data["do"], data.get("paste"), data.get("wait"), data.get("ref"))


@dataclass(frozen=True)
class Item:
    key: str | None
    status: Status
    observed: Any = None
    next_step: NextStep | None = None
    advisory: bool = False
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "observed": self.observed,
            "next_step": self.next_step.to_dict() if self.next_step else None,
            "advisory": self.advisory,
            "error_type": self.error_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Item:
        step = data.get("next_step")
        return cls(
            key=data["key"],
            status=Status(data["status"]),
            observed=data.get("observed"),
            next_step=NextStep.from_dict(step) if step else None,
            advisory=data.get("advisory", False),
            error_type=data.get("error_type"),
        )


@dataclass(frozen=True)
class Outcome:
    items: tuple[Item, ...]

    def __post_init__(self) -> None:
        items = tuple(self.items)
        object.__setattr__(self, "items", items)
        if not items:
            raise ValueError("an outcome needs at least one item")
        keys = [item.key for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("item keys must be unique")
        if len(items) > 1 and None in keys:
            raise ValueError("only a single item may be unnamed")

    @property
    def status(self) -> Status:
        blocking = {item.status for item in self.items if not item.advisory}
        for status in SEVERITY:
            if status in blocking:
                return status
        return Status.OK

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Outcome:
        return cls(tuple(Item.from_dict(item) for item in data["items"]))


def outcome(*items: Item) -> Outcome:
    return Outcome(items)


def ok(key: str | None = None, *, observed: Any = None, advisory: bool = False) -> Item:
    return Item(key, Status.OK, observed, None, advisory)


def fail(
    key: str | None = None,
    *,
    do: str,
    paste: str | None = None,
    wait: str | None = None,
    ref: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.FAIL, observed, NextStep(do, paste, wait, ref), advisory)


def pending(
    key: str | None = None,
    *,
    wait: str,
    do: str = "Nothing to do but wait.",
    paste: str | None = None,
    ref: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.PENDING, observed, NextStep(do, paste, wait, ref), advisory)


def error(
    key: str | None = None,
    *,
    do: str,
    paste: str | None = None,
    ref: str | None = None,
    error_type: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.ERROR, observed, NextStep(do, paste, None, ref), advisory, error_type)
```

In `tests/test_outcome.py`: remove `apply_remedy` from the import list; delete `test_remedy_fills_empty_fields_and_replaces_only_a_generic_do` and any other test that calls `apply_remedy`; remove `generic=True` from the `error(...)` call near line 46; delete the two assertions that build `Item(..., Status.BLOCKED ...)` (near lines 72–73) and replace them with:

```python
    assert outcome(ok("a"), fail("b", do="x", advisory=True)).status is Status.OK
```

- [ ] **Step 4: Write the failing tests for the new check API**

Replace `tests/test_check.py` with:

```python
import pytest
from fakes import IDENTITY

from preflight.check import BoundCheck, CheckCallError, check
from preflight.identity import Identity
from preflight.outcome import Outcome, ok, outcome
from preflight.probe import Probe


@check(key="name")
def sample(probe: Probe, name: str, count: int = 1, tags: tuple[str, ...] = ()) -> Outcome:
    return outcome(ok(observed=[name, count, list(tags)]))


@check(ambient=True, timeout=5)
def with_identity(probe: Probe, identity: Identity) -> Outcome:
    return outcome(ok())


def test_calling_a_check_validates_and_binds_without_observing():
    bound = sample("a", count="2")
    assert isinstance(bound, BoundCheck)
    assert bound.values == {"name": "a", "count": 2, "tags": ()}
    assert (bound.label, bound.timeout) == ("test_check.sample(a)", 60.0)
    assert bound.identity is None


def test_ids_come_from_the_module_and_function():
    assert sample.id == "test_check.sample"
    assert (sample.module, sample.name) == ("test_check", "sample")
    assert sample.file is not None and sample.file.endswith("test_check.py")
    assert with_identity(identity=IDENTITY).label == "test_check.with_identity"


def test_wrong_types_raise_a_check_call_error_naming_the_field():
    with pytest.raises(CheckCallError, match=r"^test_check\.sample: count: Input should be"):
        sample("a", count="many")


def test_missing_and_unknown_arguments_are_refused():
    with pytest.raises(CheckCallError, match="missing a required argument: 'name'"):
        sample()
    with pytest.raises(CheckCallError, match="unexpected keyword argument 'colour'"):
        sample("a", colour="red")


def test_timeout_is_a_reserved_keyword_of_every_call():
    assert sample("a", timeout=5).timeout == 5
    assert with_identity(identity=IDENTITY).timeout == 5
    with pytest.raises(CheckCallError, match="timeout: must be greater than 0"):
        sample("a", timeout=0)


def test_the_identity_argument_is_found_and_survives_json():
    bound = with_identity(identity=IDENTITY)
    assert bound.identity == IDENTITY
    again = with_identity.arguments.model_validate_json(bound.arguments_json())
    assert again.identity == IDENTITY


def test_checks_are_hashable_so_tests_can_key_stand_ins_by_check():
    assert {sample: 1}[sample] == 1


def test_definition_errors():
    def nothing() -> Outcome: ...

    def unannotated(probe, name) -> Outcome: ...

    def reserved(probe, timeout: int) -> Outcome: ...

    def two(probe, a: Identity, b: Identity | None = None) -> Outcome: ...

    def star(probe, *names: str) -> Outcome: ...

    def fine(probe, name: str) -> Outcome: ...

    with pytest.raises(TypeError, match="first parameter is the probe"):
        check(nothing)
    with pytest.raises(TypeError, match="parameter name needs a type annotation"):
        check(unannotated)
    with pytest.raises(TypeError, match="timeout is reserved"):
        check(reserved)
    with pytest.raises(TypeError, match="at most one aws.Identity"):
        check(two)
    with pytest.raises(TypeError, match=r"\*names is not supported"):
        check(star)
    with pytest.raises(TypeError, match="key other is not a parameter"):
        check(key="other")(fine)
    with pytest.raises(TypeError, match="define checks at module level"):
        check(fine)
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_check.py -q`
Expected: FAIL at import (`preflight.probe` has no `Probe`, `preflight.check` has no `BoundCheck`).

- [ ] **Step 6: Write `src/preflight/check.py`**

Replace the file with:

```python
"""Checks: functions whose calls are validated when made and observed later, in a worker."""

from __future__ import annotations

import inspect
import sys
import types
import typing
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypeVar, get_type_hints

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, create_model

from preflight.identity import Identity
from preflight.outcome import Outcome

T = TypeVar("T")


def unique_by(key: Callable[[Any], Hashable]) -> Callable[[list[T]], list[T]]:
    """A list validator refusing entries whose `key` repeats; the error names the repeated keys."""

    def validate(values: list[T]) -> list[T]:
        seen: set[Hashable] = set()
        repeated: list[Hashable] = []
        for value in values:
            marker = key(value)
            if marker in seen and marker not in repeated:
                repeated.append(marker)
            seen.add(marker)
        if repeated:
            raise ValueError(f"duplicate entries: {', '.join(map(str, repeated))}")
        return values

    return validate


unique = unique_by(lambda value: value)
# A list whose entries must differ: `UniqueList[str]`.
UniqueList = Annotated[list[T], AfterValidator(unique)]

ARGUMENTS_CONFIG = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CheckCallError(Exception):
    """A check was called with arguments that do not fit its signature; nothing was observed."""

    def __init__(self, check_id: str, problems: list[str]):
        self.check_id = check_id
        self.problems = list(problems)
        super().__init__(f"{check_id}: " + "; ".join(self.problems))


def _is_identity(annotation: Any) -> bool:
    if annotation is Identity:
        return True
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return Identity in typing.get_args(annotation)
    return False


def _module_label(module: str) -> str:
    if module == "__main__":
        file = getattr(sys.modules.get("__main__"), "__file__", None)
        return Path(file).stem if file else "__main__"
    return module.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Check:
    id: str
    observe: Callable[..., Outcome]
    arguments: type[BaseModel]
    key: str | None = None
    timeout: float = 60.0
    # Run with the caller's AWS variables intact (only aws.assumed).
    ambient: bool = False

    @property
    def module(self) -> str:
        return self.observe.__module__

    @property
    def name(self) -> str:
        return self.observe.__name__

    @property
    def file(self) -> str | None:
        return inspect.getsourcefile(self.observe)

    def __call__(self, *args: Any, timeout: float | None = None, **kwargs: Any) -> BoundCheck:
        try:
            bound = inspect.signature(self.observe).bind(None, *args, **kwargs)
        except TypeError as exc:
            raise CheckCallError(self.id, [str(exc)]) from None
        values = dict(bound.arguments)
        values.pop(next(iter(values)))  # the probe, supplied by the worker
        try:
            arguments = self.arguments.model_validate(values)
        except ValidationError as exc:
            raise CheckCallError(
                self.id,
                [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()],
            ) from None
        if timeout is not None and not timeout > 0:
            raise CheckCallError(self.id, ["timeout: must be greater than 0"])
        return BoundCheck(self, arguments, float(timeout) if timeout else self.timeout)


@dataclass(frozen=True)
class BoundCheck:
    """A check with validated arguments, ready to run in a worker."""

    check: Check
    arguments: BaseModel
    timeout: float

    @property
    def values(self) -> dict[str, Any]:
        return {name: getattr(self.arguments, name) for name in type(self.arguments).model_fields}

    @property
    def label(self) -> str:
        if self.check.key is None:
            return self.check.id
        return f"{self.check.id}({getattr(self.arguments, self.check.key)})"

    @property
    def identity(self) -> Identity | None:
        return next((v for v in self.values.values() if isinstance(v, Identity)), None)

    def arguments_json(self) -> str:
        return self.arguments.model_dump_json()


def _arguments_model(fn: Callable[..., Outcome]) -> type[BaseModel]:
    parameters = list(inspect.signature(fn).parameters.values())
    if not parameters:
        raise TypeError(f"{fn.__qualname__}: a check's first parameter is the probe")
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, Any] = {}
    for parameter in parameters[1:]:
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            star = "*" if parameter.kind is parameter.VAR_POSITIONAL else "**"
            raise TypeError(f"{fn.__qualname__}: {star}{parameter.name} is not supported")
        if parameter.name == "timeout":
            raise TypeError(f"{fn.__qualname__}: timeout is reserved for every check call")
        if parameter.name not in hints:
            raise TypeError(
                f"{fn.__qualname__}: parameter {parameter.name} needs a type annotation"
            )
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (hints[parameter.name], default)
    return create_model(f"{fn.__name__}_arguments", __config__=ARGUMENTS_CONFIG, **fields)


def check(
    fn: Callable[..., Outcome] | None = None,
    /,
    *,
    key: str | None = None,
    timeout: float = 60.0,
    ambient: bool = False,
) -> Any:
    """`@check` or `@check(key=..., timeout=..., ambient=...)` on a module-level function whose
    first parameter is the probe and whose other parameters are annotated."""

    def decorate(fn: Callable[..., Outcome]) -> Check:
        model = _arguments_model(fn)
        if key is not None and key not in model.model_fields:
            raise TypeError(f"{fn.__qualname__}: key {key} is not a parameter")
        identities = [n for n, f in model.model_fields.items() if _is_identity(f.annotation)]
        if len(identities) > 1:
            raise TypeError(f"{fn.__qualname__}: a check takes at most one aws.Identity")
        if fn.__qualname__ != fn.__name__:
            raise TypeError(f"{fn.__qualname__}: define checks at module level for workers")
        return Check(
            f"{_module_label(fn.__module__)}.{fn.__name__}", fn, model, key, timeout, ambient
        )

    return decorate(fn) if fn is not None else decorate
```

- [ ] **Step 7: Rename the probe**

In `src/preflight/probe.py` (moved from `context.py`):

1. Change the module docstring's first line to `"""What a check observes through: testinfra hosts, Ansible modules, DNS, and its identity."""` (unchanged text; keep it).
2. Replace the dataclass header and fields:

```python
@dataclass
class Probe:
    root: Path
    identity: Identity | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    _host: Any = None
    _ansible_host: Any = None
    _dns: DnsClient | None = None
```

3. Replace `_identity`, `aws_module` and `ssm_lookup` with:

```python
    def _identity(self) -> Identity:
        if self.identity is None:
            raise RuntimeError("this check has no identity")
        return self.identity

    def aws_module(
        self,
        module: str,
        args: Mapping[str, Any] | None = None,
        *,
        ambient: bool = False,
        expect: tuple[str, ...],
    ) -> dict[str, Any]:
        chosen = self._identity()
        payload = {**(args or {}), "region": chosen.region}
        if not ambient:
            payload["profile"] = chosen.profile
        result = self.ansible_host.ansible(module, json.dumps(payload), check=True)
        if (
            result.get("failed")
            or result.get("unreachable")
            or result.get("exception")
            or any(key not in result for key in expect)
        ):
            raise ModuleFailed(module)
        return result

    def ssm_lookup(self, name: str) -> str | None:
        chosen = self._identity()
        expression = (
            "{{ lookup('amazon.aws.ssm_parameter', "
            + _literal(name)
            + ", decrypt=False, on_missing='skip', profile="
            + _literal(chosen.profile)
            + ", region="
            + _literal(chosen.region)
            + ") }}"
        )
        result = self.ansible_host.ansible(
            "ansible.builtin.debug", json.dumps({"msg": expression}), check=True
        )
        value = result.get("msg")
        if result.get("failed") or (
            isinstance(value, str) and value.startswith(LOOKUP_FAILED_PREFIX)
        ):
            raise ModuleFailed("amazon.aws.ssm_parameter")
        return None if value in MISSING else str(value)
```

Keep everything else in the file (`SAFE`, `MISSING`, `LOOKUP_FAILED_PREFIX`, `ModuleFailed`, `_literal`, `make_ansible_host`, the `host`/`ansible_host`/`dns` properties, `path`).

In `tests/fakes.py`, replace `from preflight.context import Context` with `from preflight.probe import Probe`, add `from preflight.check import BoundCheck` and `from preflight.outcome import Outcome`, and replace `make_ctx` with:

```python
def make_probe(root, *, host=None, ansible=None, dns=None, identity=IDENTITY, environ=None):
    return Probe(
        root=root,
        identity=identity,
        environ=environ if environ is not None else {},
        _host=host,
        _ansible_host=ansible,
        _dns=dns,
    )


def observe(bound: BoundCheck, probe: Probe) -> Outcome:
    """Runs a bound check's function in process, with the arguments its call validated."""
    return bound.check.observe(probe, **bound.values)
```

In `tests/test_probe.py`, apply: `sed -i 's/from preflight import context as context_module/from preflight import probe as probe_module/; s/context_module/probe_module/g; s/make_ctx/make_probe/g; s/\bctx\b/probe/g' tests/test_probe.py`. Then remove the `identity=` keyword from any `aws_module(...)`/`ssm_lookup(...)` call in that file, and delete any test that passes `environment=` or `identities=` to the probe. The test `test_a_check_without_an_identity_cannot_call_aws` stays and must pass with `make_probe(tmp_path, identity=None)`.

In `tests/conftest.py`, delete the lines from `from preflight.gate import unload_consumer  # noqa: E402` to the end of the file (the `_fresh_consumer` fixture).

- [ ] **Step 8: Write the failing worker tests**

Replace `tests/test_worker.py` with:

```python
import json
import os
import sys
import time

import pytest
from conftest import write

from preflight import worker
from preflight.outcome import Status
from preflight.worker import Job, execute, kill_all, launch, reset_stop

CHECKS = """
import os
import subprocess
import time
from pathlib import Path

from preflight.check import check
from preflight.outcome import fail, ok, outcome


@check
def sample(probe, mode: str = "ok", servers: dict[str, list[str]] = {}):
    if mode == "fail":
        return outcome(fail(do="Fix it."))
    if mode == "raise":
        raise RuntimeError("password=hunter2")
    if mode == "wrong":
        return "not an outcome"
    if mode == "sleep":
        child = subprocess.Popen(["sleep", "30"])
        Path(probe.root, "child.pid").write_text(str(child.pid))
        time.sleep(30)
    if mode == "escape":
        subprocess.Popen(["setsid", "sleep", "30"])
    if mode == "escape_sleep":
        subprocess.Popen(["setsid", "sleep", "30"])
        time.sleep(30)
    if mode == "print":
        os.write(1, b"junk")
    if mode == "exit":
        os._exit(3)
    return outcome(ok(observed=servers))
"""

GATE = """
from pathlib import Path

from preflight.check import check
from preflight.outcome import ok, outcome


@check
def where(probe):
    return outcome(ok(observed=str(probe.root)))


if __name__ == "__main__":
    Path(__file__).with_name("ran-as-main").write_text("yes")
"""


def job(repo, **arguments):
    write(repo, "gate/workerchecks.py", CHECKS)
    return Job(
        label="workerchecks.sample",
        module="workerchecks",
        file=None,
        name="sample",
        gate_dir=str(repo / "gate"),
        root=str(repo),
        arguments=json.dumps(arguments),
    )


@pytest.fixture(autouse=True)
def _clean_imports():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in [n for n in sys.modules if n == "workerchecks" or n.startswith("preflight_gate_")]:
        del sys.modules[name]
    reset_stop()


def test_execute_returns_the_checks_outcome(repo):
    assert execute(job(repo, mode="fail")).status is Status.FAIL


def test_an_exception_becomes_an_error_carrying_only_its_type(repo):
    result = execute(job(repo, mode="raise"))
    assert result.items[0].error_type == "RuntimeError"
    assert "hunter2" not in json.dumps(result.to_dict())


def test_a_non_outcome_is_an_error(repo):
    assert execute(job(repo, mode="wrong")).items[0].error_type == "TypeError"


def test_arguments_that_no_longer_validate_are_an_error(repo):
    item = execute(job(repo, mode=3)).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "ValidationError")


def test_launch_runs_the_check_in_a_worker_process(repo):
    result = launch(job(repo, mode="ok", servers={"app": ["ns-1"]}), env=dict(os.environ), timeout=60)
    assert result.items[0].observed == {"app": ["ns-1"]}


def test_a_check_in_a_gate_file_loads_without_running_the_gate(repo):
    gate = write(repo, "gate/bootstrap.py", GATE)
    loaded = Job(
        label="bootstrap.where",
        module="__main__",
        file=str(gate),
        name="where",
        gate_dir=str(gate.parent),
        root=str(repo),
        arguments="{}",
    )
    result = launch(loaded, env=dict(os.environ), timeout=60)
    assert result.items[0].observed == str(repo)
    assert not (repo / "gate" / "ran-as-main").exists()


def test_a_timeout_kills_the_whole_process_group(repo):
    result = launch(job(repo, mode="sleep"), env=dict(os.environ), timeout=5)
    assert result.items[0].error_type == "Timeout"
    pid = int((repo / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the worker's child survived the timeout")


def test_stray_output_does_not_corrupt_the_result_and_crashes_are_errors(repo):
    env = dict(os.environ)
    assert launch(job(repo, mode="print"), env=env, timeout=60).status is Status.OK
    assert launch(job(repo, mode="exit"), env=env, timeout=60).items[0].error_type == (
        "WorkerCrashed"
    )


def test_a_gate_module_shadowing_a_dependency_does_not_break_the_worker(repo):
    write(repo, "gate/pydantic.py", 'raise RuntimeError("shadow")\n')
    result = launch(job(repo, mode="fail"), env=dict(os.environ), timeout=60)
    assert result.status is Status.FAIL


def test_a_detached_descendant_does_not_hold_the_result_hostage(repo):
    started = time.monotonic()
    result = launch(job(repo, mode="escape"), env=dict(os.environ), timeout=60)
    assert result.status is Status.OK
    assert time.monotonic() - started < 15


def test_a_timeout_with_a_detached_descendant_still_returns_promptly(repo):
    started = time.monotonic()
    result = launch(job(repo, mode="escape_sleep"), env=dict(os.environ), timeout=3)
    assert result.items[0].error_type == "Timeout"
    assert time.monotonic() - started < 10


def test_launch_refuses_after_kill_all_until_reset(repo, monkeypatch):
    kill_all()
    env = dict(os.environ)
    assert launch(job(repo, mode="fail"), env=env, timeout=60).items[0].error_type == (
        "Interrupted"
    )
    reset_stop()
    assert launch(job(repo, mode="fail"), env=env, timeout=60).status is Status.FAIL


def test_the_worker_writes_no_bytecode_into_the_gate_directory(repo):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    assert launch(job(repo), env=env, timeout=60).status is Status.OK
    assert list((repo / "gate").rglob("__pycache__")) == []


def test_worker_module_has_no_graph_leftovers():
    assert not hasattr(worker, "encode_data")
```

- [ ] **Step 9: Run the worker tests to verify they fail**

Run: `uv run pytest tests/test_worker.py -q`
Expected: FAIL (`Job` has no field `label`).

- [ ] **Step 10: Write `src/preflight/worker.py`**

Replace the file with:

```python
"""One bound check in its own process.

`python -P -m preflight.worker` reads a job as JSON on stdin and writes the outcome as JSON to a
private copy of the original stdout, taken before the check runs; the check's own prints go to
stderr. Nothing else leaves the process: the launcher discards stderr, and kills the whole
process group when the check runs out of time."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

from preflight.check import Check
from preflight.identity import Identity
from preflight.outcome import Outcome, error, outcome
from preflight.probe import Probe


@dataclass(frozen=True)
class Job:
    label: str
    module: str
    file: str | None
    name: str
    gate_dir: str | None
    root: str
    arguments: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Job:
        return cls(**json.loads(text))


def load_module(job: Job) -> ModuleType:
    """The check's module. A gate file loads by path under a name of its own, so its
    `if __name__ == "__main__": gate.run()` does not fire. The gate's directory is appended to
    the path, after preflight's own imports, so a gate's modules cannot shadow them."""
    if job.gate_dir and job.gate_dir not in sys.path:
        sys.path.append(job.gate_dir)
    if job.module != "__main__":
        return importlib.import_module(job.module)
    if not job.file:
        raise ImportError("the gate file is unknown")
    name = f"preflight_gate_{Path(job.file).stem}"
    spec = importlib.util.spec_from_file_location(name, job.file)
    if spec is None or spec.loader is None:
        raise ImportError(job.file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def execute(job: Job, *, probe_factory: Callable[..., Probe] = Probe) -> Outcome:
    """Runs inside the worker; every failure becomes an error outcome carrying only a type."""
    try:
        check = getattr(load_module(job), job.name)
        if not isinstance(check, Check):
            raise TypeError(f"{job.name} is not a check")
        arguments = check.arguments.model_validate_json(job.arguments)
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=f"Preflight could not load {job.label} ({type(exc).__name__}).",
                error_type=type(exc).__name__,
            )
        )
    values = {name: getattr(arguments, name) for name in type(arguments).model_fields}
    identity = next((v for v in values.values() if isinstance(v, Identity)), None)
    probe = probe_factory(root=Path(job.root), identity=identity)
    try:
        result = check.observe(probe, **values)
    except (Exception, SystemExit) as exc:
        return outcome(
            error(
                do=(
                    f"Could not observe {job.label}; "
                    "check credentials, network and tools, then rerun."
                ),
                error_type=type(exc).__name__,
            )
        )
    if not isinstance(result, Outcome):
        return outcome(
            error(
                do=(
                    f"{job.label} returned {type(result).__name__}, not an Outcome; "
                    "this is a bug in the check."
                ),
                error_type="TypeError",
            )
        )
    return result


_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()
_STOPPING = False


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def kill_all() -> None:
    """Stops every live worker and makes later launches refuse until `reset_stop`."""
    global _STOPPING
    with _LIVE_LOCK:
        _STOPPING = True
        for process in list(_LIVE):
            _kill_group(process)


def reset_stop() -> None:
    global _STOPPING
    with _LIVE_LOCK:
        _STOPPING = False


def _interrupted() -> Outcome:
    return outcome(error(do="Preflight was interrupted.", error_type="Interrupted"))


def _reap(process: subprocess.Popen) -> None:
    """Reaps a killed worker without waiting on a pipe a detached descendant may hold open."""
    if process.stdout:
        process.stdout.close()
    if process.stdin:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_group(process)


def launch(
    job: Job, *, env: Mapping[str, str], timeout: float, python: str = sys.executable
) -> Outcome:
    payload = job.to_json()
    with _LIVE_LOCK:
        if _STOPPING:
            return _interrupted()
    process = subprocess.Popen(
        [python, "-P", "-m", "preflight.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=job.root,
        env=dict(env),
        start_new_session=True,
        text=True,
    )
    with _LIVE_LOCK:
        stopping = _STOPPING
        if not stopping:
            _LIVE.add(process)
    if stopping:
        _kill_group(process)
        _reap(process)
        return _interrupted()
    try:
        try:
            stdout, _ = process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            _reap(process)
            return outcome(
                error(
                    do=(
                        f"Timed out after {timeout:g} s; rerun, or pass a larger timeout= to "
                        "the check if the probe is legitimately slow."
                    ),
                    error_type="Timeout",
                )
            )
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(process)
    if process.returncode != 0:
        return outcome(
            error(
                do=(
                    f"The worker for {job.label} exited with status {process.returncode}; "
                    "rerun, and report it if it persists."
                ),
                error_type="WorkerCrashed",
            )
        )
    try:
        return Outcome.from_dict(json.loads(stdout))
    except (ValueError, KeyError, TypeError):
        return outcome(
            error(
                do=(
                    f"The worker for {job.label} produced unreadable output; "
                    "a check may be printing to stdout."
                ),
                error_type="WorkerOutput",
            )
        )


def main() -> int:
    sys.dont_write_bytecode = True  # checks are imported from the consumer's repository
    job = Job.from_json(sys.stdin.read())
    # The result pipe moves to a private, non-inheritable descriptor, so neither the check's
    # prints nor a descendant it leaves behind can touch or hold open the result.
    result_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    result = execute(job)
    with os.fdopen(result_fd, "w") as sink:
        sink.write(json.dumps(result.to_dict(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 11: Convert `aws`, `ssm`, `tofu`, `git` and `files`**

Replace `src/preflight/catalog/ssm.py` with:

```python
"""SSM parameters another stage publishes: present and non-empty. Values are read without
decryption and never reported."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from preflight.check import UniqueList, check
from preflight.identity import Identity
from preflight.outcome import Item, Outcome, error, fail, ok, outcome
from preflight.probe import Probe

SsmName = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_.\-/]+$")]
Names = Annotated[UniqueList[SsmName], Field(min_length=1)]


def _observe(probe: Probe, identity: Identity, name: str) -> Item:
    try:
        value = probe.ssm_lookup(name)
    except Exception as exc:
        return error(
            name,
            do=(
                f"Could not read {name}; "
                f"check that profile {identity.profile} may call ssm:GetParameter."
            ),
            error_type=type(exc).__name__,
        )
    if value:
        return ok(name)
    return fail(
        name,
        do=(
            f"SSM parameter {name} does not exist in account {identity.account_id} "
            f"({identity.region}), or is empty. Publish it from the stack that owns it, "
            "then confirm:"
        ),
        paste=(
            f"aws ssm get-parameter --name {name} --profile {identity.profile} "
            f"--region {identity.region} --query Parameter.Name --output text"
        ),
    )


@check
def parameters_exist(probe: Probe, names: Names, identity: Identity) -> Outcome:
    return outcome(*(_observe(probe, identity, name) for name in names))
```

In `src/preflight/catalog/aws.py`:
- Replace the imports of `IdentitySection`, `check`, `session_for` with:
  ```python
  from preflight.check import check
  from preflight.identity import Identity
  from preflight.probe import Probe
  ```
- Set `__all__ = ["Identity", "assumed", "region", "session"]`.
- Change `_caller(ctx, *, ambient)` to `_caller(probe: Probe, *, ambient: bool)` calling `probe.aws_module(...)`, and `_profile_missing(ctx, profile)` to `_profile_missing(probe: Probe, profile: str)` calling `probe.host.run(...)`.
- Change the three checks' decorators and signatures to:
  ```python
  @check
  def session(probe: Probe, identity: Identity) -> Outcome:

  @check(ambient=True)
  def assumed(probe: Probe, identity: Identity) -> Outcome:

  @check
  def region(probe: Probe, identity: Identity) -> Outcome:
  ```
  and in each body delete the line `identity = ctx.identity` and replace `ctx.` with `probe.` (`_environment_fix(probe.environ, identity)`, `probe.host.run(...)`). Message texts do not change.

In `src/preflight/catalog/tofu.py`, replace everything from `class PlanSection` to the end of the decorator line with:

```python
@check(key="dir", timeout=600)
def plan_clean(
    probe: Probe, dir: str, var_files: tuple[str, ...] = (), identity: Identity | None = None
) -> Outcome:
```

and in the body: `ctx.path(s.dir)` → `probe.path(dir)`, `s.dir` → `dir`, `ctx.path(f)` → `probe.path(f)`, `s.var_files` → `var_files`, `ctx.host` → `probe.host`, and the pending-changes line becomes:

```python
        return outcome(
            fail(do=f"{dir} has changes that are not applied; apply them, then rerun this gate.")
        )
```

Fix the imports to `from preflight.check import check`, `from preflight.identity import Identity`, `from preflight.probe import Probe`.

In `src/preflight/catalog/git.py`, delete `class UpToDateSection`, and replace the decorator and signature with:

```python
@check
def up_to_date(probe: Probe, remote: GitName = "origin", branch: GitName = "main") -> Outcome:
```

then `s.remote` → `remote`, `s.branch` → `branch`, `ctx.` → `probe.`, drop `generic=True`, and import `check` from `preflight.check` and `Probe` from `preflight.probe`.

In `src/preflight/catalog/files.py`, replace `class FilesSection` with

```python
Paths = Annotated[UniqueList[str], Field(min_length=1)]
```

(import `Annotated` from `typing`), and each of the four checks becomes `@check` over `def present(probe: Probe, paths: Paths) -> Outcome:` (likewise `absent`, `git_ignored`, `committed`), with `s.paths` → `paths`, `ctx` → `probe` in the helpers `_exists`, `_ignored`, `_committed`, and every `generic=True` removed.

- [ ] **Step 12: Convert `sops`, `acm`, `dns` and `github`**

Apply these rules to each module, then the specific changes below.

Rules:
1. Delete each `class <Name>Section(Section)`. Its fields become the check function's parameters after `probe: Probe`, with the same names, annotations and defaults, in the same order; a `Field(...)` constraint moves into `Annotated[<type>, Field(...)]`; a mutable default `[]` becomes `()` with the annotation `tuple[<item>, ...]`.
2. `def x(ctx, s: <Name>Section)` becomes `def x(probe: Probe, <parameters>)`; in the body `s.<field>` becomes `<field>` and `ctx.` becomes `probe.`.
3. `@check("<ns>.<name>", section=..., requires=[...], timeout=T)` becomes `@check(key=..., timeout=T)` with the key from the Interfaces list (bare `@check` when there is no key and no timeout).
4. An `identity: IdentityRef` field becomes `identity: Identity`; `ctx.identity` becomes `identity`.
5. Remove every `generic=True` and `use_remedy=True`.
6. Imports: `from preflight.check import check` (plus `UniqueList`/`unique_by` where used), `from preflight.identity import Identity` where needed, `from preflight.probe import Probe`. Nothing imports `Section`, `IdentityRef`, `IdentitySection` or `session_for`.

Specific changes:
- `acm.issued(probe, arn: str, identity: Identity)`: `s.certificate_arn` → `arn`.
- `dns.delegated`: replace the section and the start of the body with

  ```python
  @check(key="root")
  def delegated(probe: Probe, root: Domain, name_servers: dict[Label, list[Domain]]) -> Outcome:
      root = norm(root)
      items = []
      for prefix, servers in name_servers.items():
          name = f"{prefix}.{root}"
          expected = {norm(server) for server in servers}
  ```

  keeping the rest of the loop (the `if not expected:` branch and after) unchanged apart from rules 2 and 5.
- `dns.undelegated(probe, root: Domain, zones: Zones)` (no default), `key="root"`.
- `dns.cname(probe, records: Records)` with `Records = Annotated[list[CnameRecord], AfterValidator(unique_by(lambda record: norm(record.name)))]` defined at module level; `CnameRecord` stays a `BaseModel`.
- `dns.caa(probe, domain: Domain, issuers: Annotated[list[Domain], Field(min_length=1)])`, `key="domain"`.
- `sops.rule(probe, paths: Annotated[UniqueList[str], Field(min_length=1)], min_recipients: Annotated[int, Field(ge=1)] = 1, config: str = ".sops.yaml")`.
- `github`: `gh_api(ctx, path)`, `gh_api_pages(ctx, path)` and `_gh_api(ctx, ...)` take `probe` instead of `ctx`. Replace `VariablesSection` and `variables` with:

  ```python
  def _variables(probe: Probe, base: str, flag: str, variables: dict[str, str]) -> Outcome:
      items = []
      for name, expected in variables.items():
          paste = f"gh variable set {shlex.quote(name)} {flag} --body {shlex.quote(expected)}"
          try:
              value = _dict(gh_api(probe, f"{base}/actions/variables/{name}")).get("value")
          except GhNotFound:
              items.append(fail(name, do=f"Create the Actions variable {name}.", paste=paste))
              continue
          except GhError as exc:
              items.append(_gh_error(name, exc))
              continue
          if value == expected:
              items.append(ok(name))
          else:
              items.append(
                  fail(
                      name,
                      do=f"Set the Actions variable {name} to {expected!r}.",
                      paste=paste,
                      observed=value,
                  )
              )
      return _all(items)


  @check(key="repo")
  def variables(probe: Probe, repo: Repo, variables: dict[Name, str]) -> Outcome:
      return _variables(probe, f"repos/{repo}", f"--repo {shlex.quote(repo)}", variables)


  @check(key="org")
  def org_variables(probe: Probe, org: Owner, variables: dict[Name, str]) -> Outcome:
      return _variables(probe, f"orgs/{org}", f"--org {shlex.quote(org)}", variables)
  ```

  and remove `model_validator` and `Self` from the imports. In `github.repo`, the failing step's text becomes `do=f"Create the repository {repo}, or correct its name in the gate{SEE}"`.

- [ ] **Step 13: Convert the catalog tests**

Rules for `tests/test_catalog_aws.py`, `test_catalog_acm.py`, `test_catalog_dns.py`, `test_catalog_github.py`, `test_catalog_repo.py`:
1. Import `make_probe`, `observe` (and `IDENTITY` where needed) from `fakes` instead of `make_ctx`; import `CheckCallError` from `preflight.check` instead of `ValidationError` from `pydantic`.
2. Every `<check>.observe(<ctx expression>, <section expression>)` becomes `observe(<check>(**<fields>), <probe expression>)`, where `<fields>` are the section's keyword arguments, `identity="admin"` becomes `identity=IDENTITY`, and `make_ctx(` becomes `make_probe(`. Module-level `SECTION = X.Section(...)` constants become `ARGS = {...}` dicts and calls become `observe(<check>(**ARGS), ...)`. In `test_catalog_aws.py`, `SECTION = IdentitySection(identity="admin")` becomes `ARGS = {"identity": IDENTITY}`.
3. Every test that builds a section expecting `ValidationError` calls the check instead and expects `CheckCallError`: `dns.CaaSection(domain="tellabs.dev", issuers=[])` → `dns.caa(domain="tellabs.dev", issuers=[])`; `pytest.raises(ValidationError, match="duplicate entries: app")` → `pytest.raises(CheckCallError, match="duplicate entries: app")`. The `DelegationSection(root=..., zones=[...], name_servers=...)` cases drop `zones` (the mapping's keys are the zones); the `zones=["app", "app"]` duplicate case is deleted, since a mapping cannot repeat a key.
4. `github.VariablesSection(variables={})` (neither owner) is deleted; add in its place:

   ```python
   def test_variables_need_an_owner():
       with pytest.raises(CheckCallError, match="missing a required argument: 'repo'"):
           github.variables(variables={})
   ```

   and every `github.VariablesSection(org=..., variables=...)` becomes `github.org_variables(org=..., variables=...)`.
5. Delete every assertion on `.next_step.generic` (`test_catalog_repo.py:160`, `test_catalog_dns.py:64,116,123`, `test_catalog_acm.py:46,61`); where the assertion also checked `status`, keep a plain status assertion.
6. In `test_catalog_dns.py`, `delegation(zones=..., servers=...)` becomes:

   ```python
   def delegation(zones=("app",), servers=None):
       return dns.delegated(
           root="tellabs.dev", name_servers={z: list(servers or SERVERS) for z in zones}
       )
   ```

   and the test of a zone with no recorded servers passes `name_servers={"app": SERVERS, "new": []}`.

Replace `tests/test_catalog_ssm.py` with:

```python
import json
import re

import pytest
from fakes import IDENTITY, FakeAnsibleHost, make_probe, observe
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import ssm
from preflight.check import CheckCallError
from preflight.outcome import Status

NAME = re.compile(r"ssm_parameter', '([^']+)'")


def store(values):
    def answer(args):
        value = values[NAME.search(args["msg"])[1]]
        if isinstance(value, BaseException):
            raise value
        return {"msg": value}

    return FakeAnsibleHost({"ansible.builtin.debug": answer})


def test_each_parameter_is_its_own_item(tmp_path):
    probe = make_probe(
        tmp_path,
        ansible=store({"/a": "1", "/b": "None", "/c": AnsibleException({"failed": True})}),
    )
    bound = ssm.parameters_exist(names=["/a", "/b", "/c"], identity=IDENTITY)
    items = {i.key: i for i in observe(bound, probe).items}
    assert items["/a"].status is Status.OK
    assert (items["/c"].status, items["/c"].error_type) == (Status.ERROR, "AnsibleException")
    missing = items["/b"]
    assert missing.status is Status.FAIL
    assert missing.next_step.do == (
        "SSM parameter /b does not exist in account 111111111111 (us-east-1), or is empty. "
        "Publish it from the stack that owns it, then confirm:"
    )
    assert missing.next_step.paste == (
        "aws ssm get-parameter --name /b --profile sandbox --region us-east-1 "
        "--query Parameter.Name --output text"
    )
    assert missing.observed is None


TASK_FAILED = (
    "Task failed: Finalization of task args for 'ansible.builtin.debug' failed: "
    "Couldn't connect to AWS: SECRET"
)


def test_lookup_failure_message_is_an_error_and_not_leaked(tmp_path):
    probe = make_probe(tmp_path, ansible=store({"/a": TASK_FAILED}))
    result = observe(ssm.parameters_exist(names=["/a"], identity=IDENTITY), probe)
    assert result.items[0].status is Status.ERROR
    assert "SECRET" not in json.dumps(result.to_dict())


def test_names_are_unique_valid_and_not_empty():
    with pytest.raises(CheckCallError, match="duplicate entries: /a"):
        ssm.parameters_exist(names=["/a", "/b", "/a"], identity=IDENTITY)
    with pytest.raises(CheckCallError, match="names"):
        ssm.parameters_exist(names=[], identity=IDENTITY)
    with pytest.raises(CheckCallError, match="names.0"):
        ssm.parameters_exist(names=["no-slash"], identity=IDENTITY)
```

Replace `tests/test_catalog_tofu.py` with:

```python
from fakes import IDENTITY, FakeHost, FakeResult, make_probe, observe

from preflight.catalog import tofu
from preflight.outcome import Status

BOUND = tofu.plan_clean(
    dir="stacks/bootstrap", var_files=["envs/dev.tfvars"], identity=IDENTITY
)


def host(init=None, plan=None, files=None):
    init = init or FakeResult(0)
    plan = plan or FakeResult(0)
    return FakeHost([(" init -input", init), (" plan -lock", plan)], files=files)


def run(tmp_path, fake):
    return observe(BOUND, make_probe(tmp_path, host=fake))


def test_a_clean_plan_is_read_only(tmp_path):
    fake = host()
    assert run(tmp_path, fake).status is Status.OK
    init, plan = fake.commands
    assert "TF_DATA_DIR=" in init and "-lockfile=readonly" in init and "-reconfigure" in init
    assert f"-var-file={tmp_path}/envs/dev.tfvars" in init
    assert "-lock=false" in plan and "-detailed-exitcode" in plan
    assert f"-chdir={tmp_path}/stacks/bootstrap" in plan


def test_pending_changes_fail_with_the_checks_own_step(tmp_path):
    item = run(tmp_path, host(plan=FakeResult(2))).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.do == (
        "stacks/bootstrap has changes that are not applied; apply them, then rerun this gate."
    )


def test_an_error_reports_only_the_first_error_line(tmp_path):
    stderr = "│ Error: No valid credential sources found\n│ \n│ detail with SECRET\n"
    item = run(tmp_path, host(plan=FakeResult(1, "", stderr))).items[0]
    assert item.status is Status.ERROR
    assert item.observed == "Error: No valid credential sources found"


def test_leftover_local_state_is_refused(tmp_path):
    fake = host(files={f"{tmp_path}/stacks/bootstrap/backend.tf.off": ""})
    item = run(tmp_path, fake).items[0]
    assert item.error_type == "LeftoverState"
    assert fake.commands == []


def test_a_missing_tofu_binary(tmp_path):
    assert run(tmp_path, host(init=FakeResult(127))).items[0].error_type == "MissingTool"


def test_the_label_names_the_directory_and_the_timeout_is_long():
    assert BOUND.label == "tofu.plan_clean(stacks/bootstrap)"
    assert BOUND.timeout == 600


def test_first_error():
    assert tofu.first_error("x\nError: boom\n") == "Error: boom"
    assert tofu.first_error("nothing") is None
    assert len(tofu.first_error("Error: " + "a" * 500)) == 200
```

- [ ] **Step 14: Update the package surface**

Replace `src/preflight/__init__.py` with:

```python
"""Operator guardrails: assertions a gate program runs before a script continues."""

from preflight.check import BoundCheck, Check, CheckCallError, UniqueList, check, unique_by
from preflight.outcome import (
    Item,
    NextStep,
    Outcome,
    Status,
    error,
    fail,
    ok,
    outcome,
    pending,
)
from preflight.probe import Probe

__version__ = "0.1.0"

__all__ = [
    "BoundCheck",
    "Check",
    "CheckCallError",
    "Item",
    "NextStep",
    "Outcome",
    "Probe",
    "Status",
    "UniqueList",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "unique_by",
]
```

- [ ] **Step 15: Run everything**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all tests pass; ruff reports nothing. If `ruff format --check` lists files, run `uv run ruff format src tests` and rerun. Also run `grep -rn "generic\|Section\|session_for\|make_ctx\|contract" src tests` and expect no matches except the word "Section" inside unrelated prose, if any.

- [ ] **Step 16: Commit**

```bash
git add -A
git commit -F - <<'EOF'
refactor(core)!: checks take their inputs as arguments

A check is a function whose call validates its arguments and returns a
bound check; the worker imports its module (a gate file by path) and
runs it. Contracts, reference resolvers, remedies, the dependency graph
and the preflight command are removed, and ssm.parameters_exist replaces
ssm.present and ssm.parameters.

BREAKING CHANGE: there is no preflight command, no contract and no
section; gates call checks with arguments.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 2: Gate inputs and dependencies

**Files:**
- Create: `src/preflight/params.py`
- Test: `tests/test_params.py`

**Interfaces:**
- Consumes: `preflight.outcome.Item`, `Outcome`.
- Produces:
  - `Arg(help: str | None = None)`, `Depends(provider: Callable[..., Any])` — frozen dataclasses used inside `Annotated[...]`.
  - `Unmet(*reasons: Item | Outcome)` with `.items: tuple[Item, ...]` and `.provider: str | None` (set by the resolver to the provider's `__name__`).
  - `ProviderFailed(provider: str, error_type: str)`, `str()` is `"provider <name> raised <Type>"`.
  - `GateDefinitionError(problems: Iterable[str])` with `.problems: list[str]`.
  - `Propagate(Exception)`: raised (as a subclass) by an executor to leave a run as it is, never wrapped as a provider or preflight failure (used by test stand-ins).
  - `ArgSpec(name: str, annotation: Any, default: Any, help: str | None)`; `EMPTY = inspect.Parameter.empty`.
  - `parameters_of(fn) -> list[Parameter]`, `collect_args(roots: Sequence[Callable], overrides: Mapping[Callable, Callable]) -> list[ArgSpec]`, `parse_args(prog: str, specs: Sequence[ArgSpec], argv: Sequence[str]) -> tuple[dict[str, Any], bool]` (raises `SystemExit(2)` after printing usage on a bad command line).
  - `Resolver(values: Mapping[str, Any], overrides: Mapping[Callable, Callable], stack: contextlib.ExitStack)` with `arguments(fn) -> dict[str, Any]`, `provide(provider) -> Any`, and `cleanup_problems: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_params.py`:

```python
import contextlib
from collections.abc import Iterator
from typing import Annotated, Literal

import pytest

from preflight.outcome import fail, outcome
from preflight.params import (
    EMPTY,
    Arg,
    ArgSpec,
    Depends,
    GateDefinitionError,
    Propagate,
    ProviderFailed,
    Resolver,
    Unmet,
    collect_args,
    parameters_of,
    parse_args,
)

Env = Annotated[Literal["dev", "prod"], Arg(help="the environment")]
CALLS: list[tuple[str, str]] = []


def settings(env: Env) -> dict:
    CALLS.append(("settings", env))
    return {"env": env}


Settings = Annotated[dict, Depends(settings)]


def uses_settings(s: Settings, env: Env) -> str:
    return f"{s['env']}-{env}"


def resolver(values=None, overrides=None):
    stack = contextlib.ExitStack()
    return Resolver(values or {}, overrides or {}, stack), stack


def test_every_parameter_needs_arg_or_depends():
    def bad(x: int): ...

    with pytest.raises(GateDefinitionError, match="bad: parameter x needs Annotated"):
        parameters_of(bad)


def test_collect_args_walks_providers_and_merges_by_name():
    specs = collect_args([uses_settings], {})
    assert specs == [ArgSpec("env", Literal["dev", "prod"], EMPTY, "the environment")]


def test_the_same_argument_with_another_type_is_a_gate_error():
    def other(env: Annotated[str, Arg()]): ...

    with pytest.raises(GateDefinitionError, match="argument env is declared twice"):
        collect_args([uses_settings, other], {})


def test_validate_is_reserved():
    def v(validate: Annotated[bool, Arg()] = False): ...

    with pytest.raises(GateDefinitionError, match="reserved"):
        collect_args([v], {})


def test_a_provider_cycle_is_a_gate_error():
    def q() -> int:
        return 0

    def p(v: Annotated[int, Depends(q)]) -> int:
        return v

    def r(v: Annotated[int, Depends(p)]) -> int:
        return v

    with pytest.raises(GateDefinitionError, match="cycle"):
        collect_args([p], {q: r})


SPECS = [
    ArgSpec("env", Literal["dev", "prod"], EMPTY, None),
    ArgSpec("jobs", int, 4, None),
]


def test_parse_args_positional_options_and_validate():
    assert parse_args("gate.py", SPECS, ["dev"]) == ({"env": "dev", "jobs": 4}, False)
    assert parse_args("gate.py", SPECS, ["prod", "--jobs", "2", "--validate"]) == (
        {"env": "prod", "jobs": 2},
        True,
    )


def test_parse_args_refuses_a_bad_choice_with_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("gate.py", SPECS, ["qa"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage: gate.py" in err and "invalid choice: 'qa'" in err


def test_parse_args_refuses_a_value_of_the_wrong_type(capsys):
    with pytest.raises(SystemExit):
        parse_args("gate.py", SPECS, ["dev", "--jobs", "many"])
    assert "argument jobs: invalid value 'many'" in capsys.readouterr().err


def test_providers_resolve_once_and_only_when_needed():
    CALLS.clear()
    r, stack = resolver({"env": "dev"})
    with stack:
        assert CALLS == []
        assert r.arguments(uses_settings) == {"s": {"env": "dev"}, "env": "dev"}
        r.arguments(uses_settings)
    assert CALLS == [("settings", "dev")]


def test_overrides_replace_providers():
    r, stack = resolver({"env": "dev"}, {settings: lambda: {"env": "fake"}})
    with stack:
        assert r.arguments(uses_settings) == {"s": {"env": "fake"}, "env": "dev"}


def test_yield_providers_clean_up_in_reverse_and_failures_are_reported():
    log = []

    def first() -> Iterator[str]:
        log.append("enter first")
        yield "1"
        log.append("exit first")

    def second(f: Annotated[str, Depends(first)]) -> Iterator[str]:
        log.append("enter second")
        yield f + "2"
        raise RuntimeError("secret value")

    def guard(s: Annotated[str, Depends(second)]): ...

    r, stack = resolver()
    assert r.arguments(guard) == {"s": "12"}
    stack.close()
    assert log == ["enter first", "enter second", "exit first"]
    assert r.cleanup_problems == ["provider second cleanup raised RuntimeError"]


def test_unmet_names_its_provider_and_carries_items():
    def signed() -> str:
        raise Unmet(outcome(fail(do="Sign in.")))

    def guard(x: Annotated[str, Depends(signed)]): ...

    r, _ = resolver()
    with pytest.raises(Unmet) as exc:
        r.arguments(guard)
    assert exc.value.provider == "signed"
    assert exc.value.items[0].next_step.do == "Sign in."


def test_unmet_before_yield_in_a_generator_provider():
    def signed() -> Iterator[str]:
        raise Unmet(fail(do="Sign in."))
        yield "never"

    def guard(x: Annotated[str, Depends(signed)]): ...

    r, _ = resolver()
    with pytest.raises(Unmet):
        r.arguments(guard)


def test_other_provider_exceptions_carry_only_their_type_and_the_inner_name():
    def inner() -> str:
        raise KeyError("account_id=123456789012")

    def outer(v: Annotated[str, Depends(inner)]) -> str:
        return v

    def guard(x: Annotated[str, Depends(outer)]): ...

    r, _ = resolver()
    with pytest.raises(ProviderFailed) as exc:
        r.arguments(guard)
    assert str(exc.value) == "provider inner raised KeyError"
    assert "123456789012" not in str(exc.value)


def test_unmet_needs_an_item():
    with pytest.raises(ValueError):
        Unmet()


def test_propagate_passes_through_providers_unwrapped():
    class Stop(Propagate):
        pass

    def stops() -> str:
        raise Stop("no stand-in")

    def guard(x: Annotated[str, Depends(stops)]): ...

    r, _ = resolver()
    with pytest.raises(Stop):
        r.arguments(guard)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_params.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'preflight.params'`.

- [ ] **Step 3: Write `src/preflight/params.py`**

```python
"""Gate inputs and dependencies, as FastAPI resolves a handler's parameters: `Arg` comes from
the command line, `Depends` from a provider called once per run, when first needed."""

from __future__ import annotations

import argparse
import contextlib
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter, ValidationError

from preflight.outcome import Item, Outcome

EMPTY = inspect.Parameter.empty


@dataclass(frozen=True)
class Arg:
    """A gate input from the command line: positional without a default, `--name` with one."""

    help: str | None = None


@dataclass(frozen=True)
class Depends:
    """A value from `provider`, called once per run with its own parameters resolved."""

    provider: Callable[..., Any]


class GateDefinitionError(Exception):
    """The gate is wrong in a way found before anything is observed (exit 2)."""

    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class Unmet(Exception):
    """A provider cannot provide. Carries the next steps; the guard needing it stops the run."""

    def __init__(self, *reasons: Item | Outcome):
        items: list[Item] = []
        for reason in reasons:
            items.extend(reason.items if isinstance(reason, Outcome) else [reason])
        if not items:
            raise ValueError("Unmet needs at least one item")
        self.items = tuple(items)
        self.provider: str | None = None
        super().__init__(f"{len(self.items)} unmet item(s)")


class Propagate(Exception):
    """Raised by an executor to leave a run as it is, never wrapped as a provider failure."""


class ProviderFailed(Exception):
    """A provider raised something other than Unmet: its name and the exception type only."""

    def __init__(self, provider: str, error_type: str):
        self.provider = provider
        self.error_type = error_type
        super().__init__(f"provider {provider} raised {error_type}")


@dataclass(frozen=True)
class Parameter:
    name: str
    annotation: Any
    default: Any
    marker: Arg | Depends


@dataclass(frozen=True)
class ArgSpec:
    name: str
    annotation: Any
    default: Any
    help: str | None


def _name(fn: Callable[..., Any]) -> str:
    return getattr(fn, "__name__", repr(fn))


def parameters_of(fn: Callable[..., Any]) -> list[Parameter]:
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception as exc:
        raise GateDefinitionError(
            [f"{_name(fn)}: its annotations cannot be read ({type(exc).__name__})"]
        ) from None
    found: list[Parameter] = []
    problems: list[str] = []
    for parameter in inspect.signature(fn).parameters.values():
        hint = hints.get(parameter.name)
        marker, base = None, hint
        if get_origin(hint) is Annotated:
            base, *extras = get_args(hint)
            marker = next((e for e in extras if isinstance(e, (Arg, Depends))), None)
        if marker is None or parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            problems.append(
                f"{_name(fn)}: parameter {parameter.name} needs Annotated[<type>, Arg()] "
                "or Annotated[<type>, Depends(<provider>)]"
            )
            continue
        found.append(Parameter(parameter.name, base, parameter.default, marker))
    if problems:
        raise GateDefinitionError(problems)
    return found


def collect_args(
    roots: Sequence[Callable[..., Any]], overrides: Mapping[Callable[..., Any], Callable[..., Any]]
) -> list[ArgSpec]:
    """Every `Arg` the roots and their providers declare, merged by name, in first-seen order."""
    specs: dict[str, ArgSpec] = {}
    problems: list[str] = []
    seen: set[Callable[..., Any]] = set()

    def visit(fn: Callable[..., Any], trail: tuple[Callable[..., Any], ...]) -> None:
        if fn in trail:
            cycle = " -> ".join(_name(f) for f in (*trail, fn))
            problems.append(f"providers depend on each other in a cycle: {cycle}")
            return
        if fn in seen:
            return
        seen.add(fn)
        for parameter in parameters_of(fn):
            if isinstance(parameter.marker, Depends):
                provider = parameter.marker.provider
                visit(overrides.get(provider, provider), (*trail, fn))
                continue
            spec = ArgSpec(
                parameter.name, parameter.annotation, parameter.default, parameter.marker.help
            )
            existing = specs.get(spec.name)
            if existing is None:
                specs[spec.name] = spec
            elif (existing.annotation, existing.default) != (spec.annotation, spec.default):
                problems.append(
                    f"argument {spec.name} is declared twice with different types or defaults"
                )

    for root in roots:
        visit(root, ())
    if "validate" in specs:
        problems.append("argument validate is reserved for --validate")
    if problems:
        raise GateDefinitionError(problems)
    return list(specs.values())


def parse_args(
    prog: str, specs: Sequence[ArgSpec], argv: Sequence[str]
) -> tuple[dict[str, Any], bool]:
    """The arguments' values and whether --validate was given; a bad command line prints usage
    and raises SystemExit(2), as argparse does."""
    parser = argparse.ArgumentParser(prog=prog)
    for spec in specs:
        choices = (
            [str(c) for c in get_args(spec.annotation)]
            if get_origin(spec.annotation) is Literal
            else None
        )
        if spec.default is EMPTY:
            parser.add_argument(spec.name, help=spec.help, choices=choices)
        else:
            parser.add_argument(
                f"--{spec.name.replace('_', '-')}",
                dest=spec.name,
                default=spec.default,
                help=spec.help,
                choices=choices,
            )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="check the gate itself without credentials; nothing is observed",
    )
    namespace = parser.parse_args(list(argv))
    values: dict[str, Any] = {}
    for spec in specs:
        raw = getattr(namespace, spec.name)
        if spec.default is not EMPTY and raw is spec.default:
            values[spec.name] = raw
            continue
        try:
            values[spec.name] = TypeAdapter(spec.annotation).validate_python(raw)
        except ValidationError:
            parser.error(f"argument {spec.name}: invalid value {raw!r}")
    return values, namespace.validate


class Resolver:
    """Resolves parameters for one run: `Arg` values, and each provider once, lazily."""

    def __init__(
        self,
        values: Mapping[str, Any],
        overrides: Mapping[Callable[..., Any], Callable[..., Any]],
        stack: contextlib.ExitStack,
    ):
        self.values = values
        self.overrides = overrides
        self.stack = stack
        self.cache: dict[Callable[..., Any], Any] = {}
        self.cleanup_problems: list[str] = []

    def arguments(self, fn: Callable[..., Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for parameter in parameters_of(fn):
            if isinstance(parameter.marker, Arg):
                resolved[parameter.name] = self.values[parameter.name]
            else:
                resolved[parameter.name] = self.provide(parameter.marker.provider)
        return resolved

    def provide(self, provider: Callable[..., Any]) -> Any:
        if provider in self.cache:
            return self.cache[provider]
        actual = self.overrides.get(provider, provider)
        name = _name(provider)
        kwargs = self.arguments(actual)
        try:
            if inspect.isgeneratorfunction(actual):
                value = self._enter(name, contextlib.contextmanager(actual)(**kwargs))
            else:
                value = actual(**kwargs)
        except Unmet as exc:
            if exc.provider is None:
                exc.provider = name
            raise
        except (ProviderFailed, Propagate):
            raise
        except Exception as exc:
            raise ProviderFailed(name, type(exc).__name__) from None
        self.cache[provider] = value
        return value

    def _enter(self, name: str, manager: contextlib.AbstractContextManager[Any]) -> Any:
        value = manager.__enter__()

        def leave(*_exc_info: Any) -> bool:
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:
                self.cleanup_problems.append(
                    f"provider {name} cleanup raised {type(exc).__name__}"
                )
            return False

        self.stack.push(leave)
        return value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_params.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

```bash
git add src/preflight/params.py tests/test_params.py
git commit -F - <<'EOF'
feat(core): Arg and Depends, resolved once per run and lazily

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 3: Executors and `probe_now`

**Files:**
- Create: `src/preflight/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `BoundCheck` (Task 1), `Job`, `launch`, `kill_all` (Task 1), `worker_environment` (unchanged).
- Produces:
  - `Executor` protocol: `run(checks: Sequence[BoundCheck]) -> list[Outcome]`, outcomes in the checks' order.
  - `WorkerExecutor(*, root: Path, gate_dir: Path, jobs: int = 4)` with `job(bound) -> Job` and `run(...)`.
  - `StandInExecutor()`: every check stands in as `ok`, observed `"not observed (--validate)"`; starts nothing.
  - `using(executor)` context manager; `probe_now(bound: BoundCheck) -> Outcome` (raises `RuntimeError` outside a run).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner.py`:

```python
import importlib
import sys
import time

import pytest
from conftest import write
from fakes import IDENTITY

from preflight import runner
from preflight.outcome import Status
from preflight.runner import StandInExecutor, WorkerExecutor, probe_now, using

CHECKS = """
import os
import time

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import fail, ok, outcome


@check(key="name")
def sample(probe, name: str, mode: str = "ok"):
    if mode == "sleep":
        time.sleep(2)
    return outcome(fail(do=f"Fix {name}.") if mode == "fail" else ok(observed=name))


@check
def environment(probe, identity: Identity):
    keys = ("AWS_PROFILE", "AWS_REGION", "AWS_ACCESS_KEY_ID")
    return outcome(ok(observed={k: os.environ.get(k) for k in keys}))
"""


@pytest.fixture
def checks(repo, monkeypatch):
    write(repo, "gate/runnerchecks.py", CHECKS)
    monkeypatch.syspath_prepend(str(repo / "gate"))
    module = importlib.import_module("runnerchecks")
    yield module
    sys.modules.pop("runnerchecks", None)


def executor(repo, jobs=4):
    return WorkerExecutor(root=repo, gate_dir=repo / "gate", jobs=jobs)


def test_checks_run_in_parallel_workers_and_keep_their_order(repo, checks):
    bound = [checks.sample(name=n, mode="sleep") for n in ("a", "b", "c")]
    started = time.monotonic()
    results = executor(repo).run(bound)
    assert time.monotonic() - started < 5
    assert [r.items[0].observed for r in results] == ["a", "b", "c"]


def test_the_job_names_the_module_and_the_arguments(repo, checks):
    job = executor(repo).job(checks.sample(name="x"))
    assert (job.label, job.module, job.name) == ("runnerchecks.sample(x)", "runnerchecks", "sample")
    assert job.gate_dir == str(repo / "gate") and job.root == str(repo)
    assert job.arguments == '{"name":"x","mode":"ok"}'


def test_an_identity_argument_sets_the_workers_profile_and_region(repo, checks, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASTRAY")
    (result,) = executor(repo).run([checks.environment(identity=IDENTITY)])
    assert result.items[0].observed == {
        "AWS_PROFILE": "sandbox",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": None,
    }


def test_stand_ins_observe_nothing(repo, checks, monkeypatch):
    def no_launch(*args, **kwargs):
        raise AssertionError("a worker was launched")

    monkeypatch.setattr(runner, "launch", no_launch)
    (result,) = StandInExecutor().run([checks.sample(name="x", mode="fail")])
    assert result.status is Status.OK
    assert result.items[0].observed == "not observed (--validate)"


def test_probe_now_runs_only_inside_a_run(repo, checks):
    with pytest.raises(RuntimeError, match="only while a gate runs"):
        probe_now(checks.sample(name="x"))
    with using(StandInExecutor()):
        assert probe_now(checks.sample(name="x")).status is Status.OK
    with using(executor(repo)):
        assert probe_now(checks.sample(name="x", mode="fail")).status is Status.FAIL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -q`
Expected: FAIL with `ImportError: cannot import name 'StandInExecutor'`.

- [ ] **Step 3: Write `src/preflight/runner.py`**

```python
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
        return [outcome(ok(observed="not observed (--validate)")) for _ in checks]


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`

```bash
git add src/preflight/runner.py tests/test_runner.py
git commit -F - <<'EOF'
feat(core): executors for workers and --validate, and probe_now

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 4: The worklist

**Files:**
- Create: `src/preflight/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Item`, `NextStep`, `Outcome`, `Status` (Task 1).
- Produces:
  - `CheckResult(label: str, outcome: Outcome)`.
  - `GuardResult(name: str, checks: tuple[CheckResult, ...] = (), unmet: tuple[Item, ...] = (), unmet_by: str | None = None)` with `passed: bool` and `labelled_items() -> list[tuple[str, Item]]`.
  - `RunResult(gate: str, arguments: tuple[str, ...] = (), exit_code: int = 0, guards: tuple[GuardResult, ...] = (), not_run: tuple[str, ...] = (), problems: tuple[str, ...] = (), validated: bool = False, output: str = "")` with `stopped_at: str | None`.
  - `worklist(result: RunResult) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render.py`:

```python
from preflight.outcome import error, fail, ok, outcome
from preflight.render import CheckResult, GuardResult, RunResult, worklist

ASSUMED = CheckResult(
    "aws.assumed",
    outcome(
        fail(
            do="Your shell acts as account 5035; the next command needs 7113.",
            paste="export AWS_PROFILE=sandbox",
        )
    ),
)


def test_a_stopped_run_lists_passed_guards_the_open_steps_and_what_did_not_run():
    result = RunResult(
        "bootstrap_entry",
        ("dev",),
        exit_code=1,
        guards=(
            GuardResult("IDs filled in", (CheckResult("iac.filled", outcome(ok("account_id"))),)),
            GuardResult(
                "this shell is the account's administrator",
                (ASSUMED, CheckResult("aws.region", outcome(ok()))),
            ),
        ),
        not_run=("the checkout holds the latest main",),
    )
    assert worklist(result) == (
        "preflight bootstrap_entry dev\n"
        "\n"
        "  ✓ IDs filled in\n"
        "  ✗ this shell is the account's administrator\n"
        "      FAIL    aws.assumed\n"
        "              Your shell acts as account 5035; the next command needs 7113.\n"
        "                export AWS_PROFILE=sandbox\n"
        "      ok      aws.region\n"
        "\n"
        "Stopped at: this shell is the account's administrator\n"
        "Not run: the checkout holds the latest main\n"
    )


def test_item_keys_extend_labels_and_identical_steps_merge():
    step = {"do": "Fill it.", "paste": "gh api orgs/x --jq .id"}
    guard = GuardResult(
        "IDs filled in",
        (
            CheckResult(
                "iac.filled(common.tfvars)",
                outcome(fail("github_org_id", **step), fail("iac_repo_id", **step)),
            ),
        ),
    )
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert (
        "      FAIL    iac.filled(common.tfvars):github_org_id, "
        "iac.filled(common.tfvars):iac_repo_id\n"
        "              Fill it.\n"
        "                gh api orgs/x --jq .id\n"
    ) in text


def test_unmet_items_show_under_the_guard_with_their_provider():
    guard = GuardResult(
        "bootstrap published",
        unmet=(error(do="Sign in to profile sandbox.", paste="aws sso login --profile sandbox"),),
        unmet_by="admin",
    )
    text = worklist(RunResult("bootstrap", ("dev",), exit_code=1, guards=(guard,)))
    assert "      ERROR   needs admin\n              Sign in to profile sandbox.\n" in text


def test_wait_ref_and_warnings():
    guard = GuardResult(
        "delegated",
        (
            CheckResult(
                "dns.cname",
                outcome(
                    fail("a.dev", do="Add the CNAME.", wait="up to an hour", ref="README"),
                    fail("b.dev", do="Add the other.", advisory=True),
                ),
            ),
        ),
    )
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert "              wait: up to an hour\n              see: README\n" in text
    assert "Warnings:\n  ! dns.cname:b.dev  Add the other.\n" in text


def test_passing_validated_and_problem_footers():
    passed = RunResult("g", guards=(GuardResult("one", (CheckResult("c", outcome(ok())),)),))
    assert worklist(passed).endswith("  ✓ one\n\nEvery guard passed.\n")
    validated = RunResult("g", ("dev", "--validate"), validated=True, guards=passed.guards)
    assert worklist(validated).endswith("Validated every guard; nothing was observed.\n")
    broken = RunResult("g", exit_code=2, problems=("provider admin raised KeyError",))
    assert worklist(broken) == "preflight g\n\n\nProblems:\n  - provider admin raised KeyError\n"


def test_stopped_at_is_the_first_guard_that_did_not_pass():
    good = GuardResult("one", (CheckResult("c", outcome(ok())),))
    bad = GuardResult("two", (CheckResult("c", outcome(fail(do="x"))),))
    assert RunResult("g", guards=(good, bad)).stopped_at == "two"
    assert RunResult("g", guards=(good,)).stopped_at is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'preflight.render'`.

- [ ] **Step 3: Write `src/preflight/render.py`**

```python
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
    lines = [STEP + step.do]
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render.py -q`
Expected: PASS. (`NextStep` is a frozen dataclass, so it is hashable and groups identical steps.)

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run ruff format --check src tests`

```bash
git add src/preflight/render.py tests/test_render.py
git commit -F - <<'EOF'
feat(core): the worklist names where a run stopped and the next step

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 5: Gates and guards

**Files:**
- Create: `src/preflight/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `BoundCheck`, `CheckCallError` (Task 1); `GateDefinitionError`, `Propagate`, `ProviderFailed`, `Resolver`, `Unmet`, `collect_args`, `parse_args` (Task 2); `Executor`, `StandInExecutor`, `WorkerExecutor`, `using` (Task 3); `CheckResult`, `GuardResult`, `RunResult`, `worklist` (Task 4); `kill_all`, `reset_stop` (Task 1).
- Produces:
  - `Guard(name: str, fn: Callable)`.
  - `Guards()` with `guard(name) -> decorator`, `include(guards: Guards)`, `flatten() -> list[Guard]`.
  - `Gate(name: str, *, jobs: int = 4)` (a `Guards`) with `.file: Path | None` (the defining file), `.dependency_overrides: dict`, `run(argv=None) -> NoReturn`, `execute(argv, *, executor=None, root=None, progress=None) -> RunResult` (with `.output` set to the worklist).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gate.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

import pytest

from preflight import gate as gate_module
from preflight.check import check
from preflight.gate import Gate, Guards
from preflight.outcome import Outcome, fail, ok, outcome
from preflight.params import Arg, Depends, Unmet
from preflight.probe import Probe

Env = Annotated[Literal["dev", "prod"], Arg()]


@check(key="name")
def thing(probe: Probe, name: str) -> Outcome:
    return outcome(ok())


class Scripted:
    """Answers checks by label; records what ran."""

    def __init__(self, answers=None, raise_on=None, exc=None):
        self.answers = answers or {}
        self.ran: list[str] = []
        self.raise_on, self.exc = raise_on, exc

    def run(self, checks):
        self.ran.extend(c.label for c in checks)
        if self.raise_on in self.ran:
            raise self.exc
        return [self.answers.get(c.label, outcome(ok())) for c in checks]


def three_guards():
    gate = Gate("sample")

    @gate.guard("first")
    def first(env: Env):
        return [thing(name=f"{env}-1")]

    @gate.guard("second")
    def second():
        return [thing(name="2a"), thing(name="2b")]

    @gate.guard("third")
    def third():
        return [thing(name="3")]

    return gate


def run(gate, argv=("dev",), executor=None, tmp=Path(".")):
    return gate.execute(list(argv), executor=executor or Scripted(), root=tmp)


def test_guards_run_in_order_and_everything_passing_exits_0(tmp_path):
    executor = Scripted()
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 0
    assert executor.ran == [
        "test_gate.thing(dev-1)",
        "test_gate.thing(2a)",
        "test_gate.thing(2b)",
        "test_gate.thing(3)",
    ]
    assert result.output.endswith("Every guard passed.\n")


def test_the_first_guard_that_does_not_pass_stops_the_run(tmp_path):
    executor = Scripted({"test_gate.thing(2b)": outcome(fail(do="Fix 2b."))})
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 1
    assert result.stopped_at == "second"
    assert result.not_run == ("third",)
    assert "test_gate.thing(3)" not in executor.ran
    assert "Fix 2b." in result.output


def test_include_places_guards_where_it_is_called(tmp_path):
    shared = Guards()

    @shared.guard("shared")
    def shared_guard():
        return [thing(name="s")]

    gate = Gate("g")

    @gate.guard("before")
    def before():
        return [thing(name="b")]

    gate.include(shared)

    @gate.guard("after")
    def after():
        return [thing(name="a")]

    executor = Scripted()
    run(gate, argv=(), executor=executor, tmp=tmp_path)
    assert executor.ran == ["test_gate.thing(b)", "test_gate.thing(s)", "test_gate.thing(a)"]


def test_repeated_labels_are_numbered(tmp_path):
    gate = Gate("g")

    @gate.guard("twice")
    def twice():
        return [thing(name="x"), thing(name="x")]

    executor = Scripted({"test_gate.thing(x)": outcome(fail(do="No."))})
    result = run(gate, argv=(), executor=executor, tmp=tmp_path)
    labels = [c.label for c in result.guards[0].checks]
    assert labels == ["test_gate.thing(x) #1", "test_gate.thing(x) #2"]


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (lambda: thing(name="x"), "guard 'bad' must return a non-empty list of checks"),
        (lambda: [], "guard 'bad' must return a non-empty list of checks"),
        (lambda: [thing(name=3)], "guard 'bad': test_gate.thing: name: Input should be"),
        (lambda: {}["missing"], "guard 'bad' raised KeyError"),
    ],
)
def test_a_wrong_guard_is_a_gate_error(tmp_path, body, problem):
    gate = Gate("g")
    gate.guard("bad")(body)
    executor = Scripted()
    result = run(gate, argv=(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 2
    assert any(p.startswith(problem) for p in result.problems)
    assert executor.ran == []


def test_definition_errors_exit_2_before_anything_runs(tmp_path):
    gate = Gate("g")
    gate.guard("same")(lambda: [thing(name="1")])
    gate.guard("same")(lambda: [thing(name="2")])
    assert run(gate, argv=(), tmp=tmp_path).problems == ("guard name used twice: same",)
    empty = Gate("empty")
    assert run(empty, argv=(), tmp=tmp_path).problems == ("gate empty has no guards",)

    def untyped(x):
        return [thing(name="x")]

    loose = Gate("loose")
    loose.guard("loose")(untyped)
    result = run(loose, argv=(), tmp=tmp_path)
    assert result.exit_code == 2
    assert "parameter x needs Annotated" in result.problems[0]


def test_a_bad_command_line_exits_2_without_running(tmp_path, capsys):
    executor = Scripted()
    result = run(three_guards(), argv=("qa",), executor=executor, tmp=tmp_path)
    assert (result.exit_code, executor.ran) == (2, [])
    assert "invalid choice: 'qa'" in capsys.readouterr().err


def test_provider_and_guard_exceptions_report_only_their_type(tmp_path):
    def admin(env: Env) -> str:
        raise KeyError("account_id=123456789012")

    gate = Gate("g")

    @gate.guard("needs admin")
    def needs(identity: Annotated[str, Depends(admin)]):
        return [thing(name=identity)]

    result = run(gate, tmp=tmp_path)
    assert result.exit_code == 2
    assert result.problems == ("guard 'needs admin': provider admin raised KeyError",)
    assert "123456789012" not in result.output


def test_unmet_stops_the_run_with_the_providers_steps(tmp_path):
    def admin() -> str:
        raise Unmet(fail(do="Sign in to profile sandbox."))

    gate = Gate("g")

    @gate.guard("needs admin")
    def needs(identity: Annotated[str, Depends(admin)]):
        return [thing(name=identity)]

    result = run(gate, argv=(), tmp=tmp_path)
    assert (result.exit_code, result.stopped_at) == (1, "needs admin")
    assert "FAIL    needs admin\n              Sign in to profile sandbox." in result.output


def cleaned_gate(log, cleanup_raises=False):
    def session() -> Iterator[str]:
        log.append("enter")
        yield "s"
        log.append("exit")
        if cleanup_raises:
            raise RuntimeError("secret")

    gate = Gate("g")

    @gate.guard("uses session")
    def first(s: Annotated[str, Depends(session)]):
        return [thing(name="1")]

    @gate.guard("second")
    def second():
        return [thing(name="2")]

    return gate


def test_cleanup_runs_on_pass_and_on_stop(tmp_path):
    log = []
    assert run(cleaned_gate(log), argv=(), tmp=tmp_path).exit_code == 0
    assert log == ["enter", "exit"]
    log.clear()
    stop = Scripted({"test_gate.thing(2)": outcome(fail(do="x"))})
    assert run(cleaned_gate(log), argv=(), executor=stop, tmp=tmp_path).exit_code == 1
    assert log == ["enter", "exit"]


def test_a_failing_cleanup_turns_a_pass_into_3_and_leaves_a_stop_at_1(tmp_path):
    passed = run(cleaned_gate([], cleanup_raises=True), argv=(), tmp=tmp_path)
    assert passed.exit_code == 3
    assert passed.problems == ("provider session cleanup raised RuntimeError",)
    stop = Scripted({"test_gate.thing(2)": outcome(fail(do="x"))})
    stopped = run(cleaned_gate([], cleanup_raises=True), argv=(), executor=stop, tmp=tmp_path)
    assert stopped.exit_code == 1


def test_an_interrupt_kills_workers_cleans_up_and_exits_130(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(gate_module, "kill_all", lambda: killed.append(True))
    log = []
    executor = Scripted(raise_on="test_gate.thing(1)", exc=KeyboardInterrupt())
    result = run(cleaned_gate(log), argv=(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 130
    assert killed == [True]
    assert log == ["enter", "exit"]
    assert "Every guard passed" not in result.output


def test_an_unexpected_executor_failure_is_a_preflight_bug(tmp_path):
    executor = Scripted(raise_on="test_gate.thing(dev-1)", exc=RuntimeError("secret"))
    result = run(three_guards(), executor=executor, tmp=tmp_path)
    assert result.exit_code == 3
    assert result.problems == ("preflight failed (RuntimeError)",)


def test_validate_observes_nothing_and_exits_0(tmp_path):
    executor = Scripted({"test_gate.thing(dev-1)": outcome(fail(do="x"))})
    result = run(three_guards(), argv=("dev", "--validate"), executor=executor, tmp=tmp_path)
    assert (result.exit_code, executor.ran) == (0, [])
    assert result.validated
    assert result.output.endswith("Validated every guard; nothing was observed.\n")


def test_the_gate_knows_its_file_and_run_exits_with_the_code(tmp_path, capsys, monkeypatch):
    gate = three_guards()
    assert gate.file == Path(__file__).resolve()
    monkeypatch.setattr(gate, "execute", lambda argv, **kw: run(three_guards(), tmp=tmp_path))
    with pytest.raises(SystemExit) as exc:
        gate.run(["dev"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.endswith("Every guard passed.\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'preflight.gate'`.

- [ ] **Step 3: Write `src/preflight/gate.py`**

```python
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
                raise GateDefinitionError(
                    [f"include takes a Guards(), not {type(entry).__name__}"]
                )
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

        result = self.execute(sys.argv[1:] if argv is None else argv, progress=progress)
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
            stack.close()
            raise
        except Exception as exc:
            kill_all()
            exit_code = 3
            problems.append(f"preflight failed ({type(exc).__name__})")
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
        except Exception as exc:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run ruff format --check src tests`

```bash
git add src/preflight/gate.py tests/test_gate.py
git commit -F - <<'EOF'
feat(core): gates run guards in order and stop at the first that fails

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 6: `aws.signed_in` and `GateClient`

**Files:**
- Modify: `src/preflight/catalog/aws.py`
- Create: `src/preflight/testing.py`
- Test: `tests/test_catalog_aws.py` (append), `tests/test_testing.py`

**Interfaces:**
- Consumes: `probe_now`, `using`, `StandInExecutor` (Task 3); `Unmet`, `Propagate` (Task 2); `Gate` (Task 5); `aws.session` (Task 1).
- Produces:
  - `aws.signed_in(**fields) -> ContextManager[Identity]` (fields are `Identity`'s: `profile`, `region`, `account_id`, and `role` or `permission_set`).
  - `preflight.testing.GateClient(gate, outcomes: Mapping[Check, Outcome | Callable[[BoundCheck], Outcome]] | None = None, *, root: Path | None = None)` with `run(argv=()) -> RunResult`; `StandIns`; `MissingStandIn(Propagate)`.

- [ ] **Step 1: Write the failing tests**

Add to the import block at the top of `tests/test_catalog_aws.py`:

```python
import pytest
from fakes import IDENTITY

from preflight.outcome import fail, ok, outcome
from preflight.params import Unmet
from preflight.runner import StandInExecutor, using
```

(merging with names already imported there), then append:

```python
FIELDS = {
    "profile": "sandbox",
    "region": "us-east-1",
    "account_id": "111111111111",
    "permission_set": "AWSAdministratorAccess",
}


class Answer:
    def __init__(self, result):
        self.result, self.ran = result, []

    def run(self, checks):
        self.ran.extend(c.label for c in checks)
        return [self.result for _ in checks]


def test_signed_in_yields_the_identity_once_the_session_is_ok():
    executor = Answer(outcome(ok()))
    with using(executor), aws.signed_in(**FIELDS) as identity:
        assert identity == IDENTITY
    assert executor.ran == ["aws.session"]


def test_signed_in_raises_unmet_with_the_sessions_steps():
    step = fail(do="Sign in to profile sandbox.", paste="aws sso login --profile sandbox")
    with using(Answer(outcome(step))), pytest.raises(Unmet) as exc:
        with aws.signed_in(**FIELDS):
            pass
    assert exc.value.items == (step,)


def test_signed_in_under_validate_yields_unverified():
    with using(StandInExecutor()), aws.signed_in(**FIELDS) as identity:
        assert identity.profile == "sandbox"
```

Create `tests/test_testing.py`:

```python
from typing import Annotated, Literal

import pytest

from preflight.catalog import aws, ssm
from preflight.gate import Gate
from preflight.outcome import fail, ok, outcome
from preflight.params import Arg, Depends
from preflight.testing import GateClient, MissingStandIn

Env = Annotated[Literal["dev", "prod"], Arg()]
IDENTITY = aws.Identity(
    profile="sandbox",
    region="us-east-1",
    account_id="111111111111",
    permission_set="AWSAdministratorAccess",
)


def admin(env: Env):
    with aws.signed_in(**IDENTITY.model_dump(exclude_none=True)) as identity:
        yield identity


Admin = Annotated[aws.Identity, Depends(admin)]

gate = Gate("bootstrap")


@gate.guard("bootstrap published")
def published(identity: Admin):
    return [ssm.parameters_exist(names=["/platform/state/bucket"], identity=identity)]


def test_stand_ins_answer_by_check_including_the_providers_session(tmp_path):
    missing = outcome(fail("/platform/state/bucket", do="Publish it."))
    client = GateClient(
        gate, {aws.session: outcome(ok()), ssm.parameters_exist: missing}, root=tmp_path
    )
    result = client.run(["dev"])
    assert (result.exit_code, result.stopped_at) == (1, "bootstrap published")
    assert "Publish it." in result.output


def test_dependency_overrides_replace_providers(tmp_path):
    gate.dependency_overrides[admin] = lambda: IDENTITY
    try:
        client = GateClient(gate, {ssm.parameters_exist: outcome(ok("x"))}, root=tmp_path)
        assert client.run(["dev"]).exit_code == 0
    finally:
        gate.dependency_overrides.clear()


def test_a_check_without_a_stand_in_fails_the_test(tmp_path):
    with pytest.raises(MissingStandIn, match="aws.session"):
        GateClient(gate, {}, root=tmp_path).run(["dev"])


def test_callables_can_answer_from_the_bound_arguments(tmp_path):
    def answer(bound):
        return outcome(*(ok(name) for name in bound.values["names"]))

    client = GateClient(
        gate, {aws.session: outcome(ok()), ssm.parameters_exist: answer}, root=tmp_path
    )
    assert client.run(["dev"]).exit_code == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_catalog_aws.py tests/test_testing.py -q`
Expected: FAIL (`aws` has no `signed_in`; no module `preflight.testing`).

- [ ] **Step 3: Add `signed_in` to `src/preflight/catalog/aws.py`**

Add the imports:

```python
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from preflight.outcome import Status
from preflight.params import Unmet
from preflight.runner import probe_now
```

(merge `Status` into the existing `preflight.outcome` import), set `__all__ = ["Identity", "assumed", "region", "session", "signed_in"]`, extend the module docstring's first sentence with `, and \`signed_in\`, the helper a gate's provider uses to yield a verified identity`, and append:

```python
@contextmanager
def signed_in(**fields: Any) -> Iterator[Identity]:
    """Yields the identity once its profile signs in as it; otherwise raises Unmet carrying
    `aws.session`'s next step (for example the `aws sso login` to paste)."""
    identity = Identity(**fields)
    result = probe_now(session(identity=identity))
    if result.status is not Status.OK:
        raise Unmet(result)
    yield identity
```

- [ ] **Step 4: Write `src/preflight/testing.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS (whole suite).

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run ruff format --check src tests`

```bash
git add src/preflight/catalog/aws.py src/preflight/testing.py tests/test_catalog_aws.py tests/test_testing.py
git commit -F - <<'EOF'
feat(catalog): aws.signed_in, and GateClient for testing gates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 7: Package surface, end to end, README

**Files:**
- Modify: `src/preflight/__init__.py`
- Create: `tests/test_end_to_end.py`
- Rewrite: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `from preflight import Arg, Depends, Gate, Guards, Unmet, probe_now` in addition to Task 1's names; importing `preflight` sets `sys.dont_write_bytecode = True`.

- [ ] **Step 1: Write the failing end-to-end tests**

Create `tests/test_end_to_end.py`:

```python
"""A gate script run as a program against a scratch repository: real workers, real testinfra,
no network."""

import subprocess
import sys

from conftest import write

HELPERS = '''
from preflight import Outcome, Probe, check, fail, ok, outcome


@check(key="name")
def marker(probe: Probe, name: str) -> Outcome:
    present = (probe.root / name).exists()
    return outcome(ok() if present else fail(do=f"Create {name}."))
'''

GATE = '''
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from preflight import Arg, Depends, Gate, Guards, Outcome, Probe, check, fail, ok, outcome
from preflight.catalog import files

import helpers

Env = Annotated[Literal["dev", "prod"], Arg()]
LOG = Path(__file__).resolve().parent.parent / "cleanup.log"


@check
def env_file(probe: Probe, env: str) -> Outcome:
    path = probe.root / f"{env}.env"
    return outcome(ok() if path.exists() else fail(do=f"Create {env}.env."))


def logged(env: Env) -> Iterator[str]:
    yield env
    LOG.write_text(env)


Logged = Annotated[str, Depends(logged)]

shared = Guards()


@shared.guard("readme present")
def readme():
    return [files.present(paths=["README.md"])]


gate = Gate("ready")
gate.include(shared)


@gate.guard("environment ready")
def environment(env: Env, seen: Logged):
    return [env_file(env=env), helpers.marker(name="MARKER")]


if __name__ == "__main__":
    gate.run()
'''


def setup(repo):
    write(repo, "gate/helpers.py", HELPERS)
    return write(repo, "gate/ready.py", GATE)


def run(gate_file, *args, cwd):
    return subprocess.run(
        [sys.executable, str(gate_file), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_first_failing_guard_stops_the_run(repo):
    gate = setup(repo)
    result = run(gate, "dev", cwd=repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "  ✗ readme present\n" in result.stdout
    assert "Create README.md." in result.stdout
    assert "Not run: environment ready" in result.stdout
    assert not (repo / "cleanup.log").exists()


def test_a_check_in_the_gate_file_and_one_in_a_helper_run_in_workers(repo):
    gate = setup(repo)
    write(repo, "README.md", "x\n")
    result = run(gate, "dev", cwd=repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Create dev.env." in result.stdout
    assert "Create MARKER." in result.stdout
    assert (repo / "cleanup.log").read_text() == "dev"


def test_a_gate_runs_from_any_directory(repo, tmp_path_factory):
    gate = setup(repo)
    for name in ("README.md", "prod.env", "MARKER"):
        write(repo, name, "x\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    result = run(gate, "prod", cwd=elsewhere)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith("Every guard passed.\n")
    assert "… readme present" in result.stderr


def test_a_bad_argument_prints_usage_and_exits_2(repo):
    result = run(setup(repo), "qa", cwd=repo)
    assert result.returncode == 2
    assert "invalid choice: 'qa'" in result.stderr


def test_validate_observes_nothing(repo):
    result = run(setup(repo), "dev", "--validate", cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated every guard; nothing was observed." in result.stdout
    assert (repo / "cleanup.log").read_text() == "dev"


def test_no_bytecode_is_left_in_the_repository(repo):
    gate = setup(repo)
    run(gate, "dev", cwd=repo)
    assert list(repo.rglob("__pycache__")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_end_to_end.py -q`
Expected: FAIL (`cannot import name 'Arg' from 'preflight'`).

- [ ] **Step 3: Complete the package surface**

Replace `src/preflight/__init__.py` with:

```python
"""Operator guardrails: assertions a gate program runs before a script continues."""

import sys

# Gates import their own modules from the consumer's repository; leave no bytecode there.
sys.dont_write_bytecode = True

from preflight.check import (  # noqa: E402
    BoundCheck,
    Check,
    CheckCallError,
    UniqueList,
    check,
    unique_by,
)
from preflight.gate import Gate, Guards  # noqa: E402
from preflight.outcome import (  # noqa: E402
    Item,
    NextStep,
    Outcome,
    Status,
    error,
    fail,
    ok,
    outcome,
    pending,
)
from preflight.params import Arg, Depends, Unmet  # noqa: E402
from preflight.probe import Probe  # noqa: E402
from preflight.runner import probe_now  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "Arg",
    "BoundCheck",
    "Check",
    "CheckCallError",
    "Depends",
    "Gate",
    "Guards",
    "Item",
    "NextStep",
    "Outcome",
    "Probe",
    "Status",
    "UniqueList",
    "Unmet",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "probe_now",
    "unique_by",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS (whole suite, including `test_package.py`).

- [ ] **Step 5: Rewrite `README.md`**

Replace the file with:

````markdown
# preflight

Guardrails for the steps only the operator can do. A script runs a gate first and stops unless
it exits 0; when something is not done, the gate names the next step, with text to paste.

```bash
# top of scripts/bootstrap.sh in a consumer repository
uv run --script "$root/preflight/bootstrap_entry.py" "$env" || exit 1
```

Preflight never runs the command it gates and never changes what it observes. It is an
assertion library: the gate is a small Python program in the consumer's repository that reads
its own inputs, from wherever the consumer keeps them, and calls preflight's checks with them.
The design follows FastAPI: guards are decorated functions, inputs and shared values are
parameters resolved by `Arg` and `Depends`, and `yield` dependencies clean up after the run.

## A gate

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["preflight @ git+https://github.com/strider4560/preflight@v0.1.0"]
# ///
"""Bootstrap is applied: its parameters are published and the stack plans clean."""

from collections.abc import Iterator
from typing import Annotated, Literal

from preflight import Arg, Depends, Gate
from preflight.catalog import aws, ssm, tofu

Env = Annotated[Literal["dev", "prod"], Arg(help="dev or prod")]
PROFILES = {"dev": "sandbox", "prod": "production"}
ACCOUNTS = {"dev": "711387098919", "prod": "503561451926"}


def admin(env: Env) -> Iterator[aws.Identity]:
    with aws.signed_in(
        profile=PROFILES[env],
        region="us-east-1",
        account_id=ACCOUNTS[env],
        permission_set="AWSAdministratorAccess",
    ) as identity:
        yield identity


Admin = Annotated[aws.Identity, Depends(admin)]

gate = Gate("bootstrap")


@gate.guard("bootstrap published")
def published(env: Env, identity: Admin):
    return [
        ssm.parameters_exist(["/platform/state/bucket"], identity=identity),
        tofu.plan_clean("stacks/bootstrap", var_files=[f"envs/{env}.tfvars"], identity=identity),
    ]


if __name__ == "__main__":
    gate.run()
```

- **Guards** run in declaration order. Each returns a list of checks, which run in parallel,
  each in its own worker process. The first guard that does not pass stops the run; the
  worklist shows what passed, that guard's open steps, and the guards that did not run.
- **`Arg()`** parameters come from the command line (positional without a default, `--name`
  with one). **`Depends(provider)`** calls the provider once per run, when a guard first needs
  it. A provider that `yield`s cleans up after the run. A provider raises **`Unmet`** with
  items to stop the guard that needed it with those next steps.
- **`Guards()`** holds guards two gates share: `gate.include(shared)` runs them where the call
  appears.
- Keep a gate file's top level to definitions: workers load it (under another module name) to
  run the checks defined in it.
- Import `preflight` before the gate's own modules: it stops Python writing bytecode into the
  repository.

## Your own checks

```python
from preflight import Outcome, Probe, check, fail, ok, outcome


@check(key="file")
def filled(probe: Probe, file: str, keys: list[str], placeholders: list[str]) -> Outcome:
    values = read_my_file(probe.root / file)
    return outcome(
        *(fail(k, do=f"Fill {k} in {file}.") if values.get(k) in placeholders else ok(k)
          for k in keys)
    )
```

Calling a check validates its arguments against its type hints and returns a bound check; a
wrong type stops the gate with exit 2 before anything is observed. Every call also takes
`timeout=` (seconds). A check observes through `probe.host` (testinfra), `probe.ansible_host`
and `probe.aws_module(module, args, expect=(...))` (Ansible modules with the identity's profile
and region), `probe.ssm_lookup(name)`, `probe.dns` and `probe.root`, and returns an `Outcome`
of items: `ok`, `fail` (the operator has something to do), `pending` (done, settling), `error`
(could not observe), optionally `advisory`. Its next step is its own text. Never put a secret
value in an item. Define checks at module level, in the gate file or a module beside it.

## Catalog

| Check | Arguments | What it proves |
|---|---|---|
| `aws.assumed` | `identity` | The caller's own shell acts as the identity |
| `aws.region` | `identity` | The profile's configured region |
| `aws.session` | `identity` | Preflight can observe as the identity (used by `aws.signed_in`) |
| `ssm.parameters_exist` | `names`, `identity` | Each parameter exists and is non-empty (read without decryption) |
| `acm.issued` | `arn`, `identity` | Issued; or the exact validation CNAME to add; or waiting |
| `tofu.plan_clean` | `dir`, `var_files`, `identity` | `tofu plan -detailed-exitcode` is 0 (no lock, read-only) |
| `dns.delegated` | `root`, `name_servers` | The parent zone's own servers delegate exactly those servers |
| `dns.undelegated` | `root`, `zones` | The parent gives no referral |
| `dns.cname`, `dns.caa` | `records`; `domain`, `issuers` | Records the operator adds by hand |
| `github.auth`, `repo`, `variables`, `org_variables`, `environments`, `ruleset`, `secret_names`, `workflow_green` | see each function | GitHub settings; secrets by name only |
| `git.up_to_date` | `remote`, `branch` | The checkout contains the remote branch's latest commit (no fetch) |
| `files.present`, `absent`, `git_ignored`, `committed` | `paths` | Files and their git status |
| `sops.rule` | `paths`, `min_recipients`, `config` | A creation rule with enough age recipients covers each file |

Checks shell out to these tools, each needed only by the checks that use it: the `aws` CLI
(`aws.region`), `tofu` (`tofu.plan_clean`), `gh` 2.48 or newer (`github.*`), and `git`
(`git.*`, `files.git_ignored`, `files.committed`; a gate must live in a git work tree).

## Running a gate

| Exit | Meaning |
|---|---|
| 0 | Every guard passed |
| 1 | A guard stopped the run |
| 2 | The gate is wrong (bad arguments, a check called with the wrong types, a guard returning something other than checks, an exception in the gate's own code); nothing more was observed |
| 3 | Preflight itself failed |
| 130 | Interrupted |

`--validate` runs the gate without credentials and observes nothing: it parses the arguments,
resolves the gate's providers (so its own file reading runs) and validates every check's
arguments. Add it to CI.

Each check runs in its own worker process with a cleaned environment: stray AWS credential
variables, `TF_CLI_ARGS*`, `TF_WORKSPACE` and `TF_VAR_*` are removed, and an `aws.Identity`
argument sets the worker's profile and region (`aws.assumed` alone keeps the caller's
variables). Timeouts kill the worker's whole process group. An exception is reported by its
type only.

## Testing a gate

```python
from preflight.testing import GateClient

gate.dependency_overrides[admin] = lambda: aws.Identity(...)
client = GateClient(gate, {ssm.parameters_exist: outcome(ok("x")), tofu.plan_clean: outcome(ok())})
assert client.run(["dev"]).exit_code == 0
```

## Developing preflight

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

The design is `docs/superpowers/specs/2026-09-29-preflight-gate-programs-design.md`.
````

- [ ] **Step 6: Lint and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

```bash
git add -A
git commit -F - <<'EOF'
feat(core): gate programs end to end, and a README for them

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 8: iac — the bootstrap gates as programs

Work in the existing worktree `~/Develop/tellabs/iac-preflight-bootstrap` on branch `feat/preflight-bootstrap` (PR tellabsadmin/iac#9). Commit on top; never rewrite pushed history.

**Prerequisite (controller):** preflight's `feat/gate-programs` must be pushed so the gates can pin it. Ask the operator before pushing. Then record the commit: `PIN=$(git -C ~/Develop/preflight rev-parse HEAD)`.

**Files (in iac):**
- Delete: `preflight/contracts/`, `preflight/gates/`, `preflight/checks/`
- Create: `preflight/iac.py`, `preflight/bootstrap_entry.py`, `preflight/bootstrap.py`
- Modify: `scripts/bootstrap.sh`, `README.md`, `Taskfile.yml`, `.github/workflows/platform.yml`

**Interfaces:**
- Consumes: preflight's `Arg`, `Depends`, `Gate`, `Guards`, `check`, `Probe`, `outcome`, `ok`, `fail`; `aws.signed_in`, `aws.assumed`, `aws.region`, `aws.Identity`; `ssm.parameters_exist`; `tofu.plan_clean`; `git.up_to_date`.
- Produces (in `preflight/iac.py`): `tfvars(relative, root=ROOT) -> dict`, `filled` check (`iac.filled(file)`), `Env`, `PROFILES`, `admin`, `Admin`, `ids_filled: Guards`.

- [ ] **Step 1: Remove the contract-era files**

```bash
cd ~/Develop/tellabs/iac-preflight-bootstrap
git rm -rq preflight/contracts preflight/gates preflight/checks
```

- [ ] **Step 2: Write `preflight/iac.py`**

```python
"""What iac's gates share: reading tfvars, the filled-in check, the environment argument, the
administrator identity, and the guard both bootstrap gates run first."""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, Literal

from preflight import Arg, Depends, Guards, Outcome, Probe, check, fail, ok, outcome
from preflight.catalog import aws

import hcl2
from hcl2 import SerializationOptions

ROOT = Path(__file__).resolve().parent.parent
HCL = SerializationOptions(strip_string_quotes=True, preserve_heredocs=False, with_comments=False)
PROFILES = {"dev": "sandbox", "prod": "production"}
# Where each ID comes from, pasted when it is still a placeholder.
HOW = {
    "github_org_id": "gh api orgs/tellabsadmin --jq .id",
    "iac_repo_id": "gh api repos/tellabsadmin/iac --jq .id",
}

Env = Annotated[Literal["dev", "prod"], Arg(help="dev (Sandbox) or prod (Production)")]


def tfvars(relative: str, root: Path = ROOT) -> dict[str, Any]:
    return hcl2.loads((root / relative).read_text(), serialization_options=HCL)


@check(key="file")
def filled(probe: Probe, file: str, keys: list[str], placeholders: list[str]) -> Outcome:
    values = tfvars(file, probe.root)
    return outcome(
        *(
            fail(key, do=f"Fill {key} in {file}.", paste=HOW.get(key))
            if values.get(key, "") in placeholders
            else ok(key)
            for key in keys
        )
    )


ids_filled = Guards()


@ids_filled.guard("IDs filled in")
def ids(env: Env):
    return [
        filled(file=f"envs/{env}.tfvars", keys=["account_id"], placeholders=["", "000000000000"]),
        filled(
            file="stacks/bootstrap/envs/common.tfvars",
            keys=["github_org_id", "iac_repo_id"],
            placeholders=["", "0"],
        ),
    ]


def admin(env: Env) -> Iterator[aws.Identity]:
    values = tfvars(f"envs/{env}.tfvars")
    with aws.signed_in(
        profile=PROFILES[env],
        region=values["region"],
        account_id=values["account_id"],
        permission_set="AWSAdministratorAccess",
    ) as identity:
        yield identity


Admin = Annotated[aws.Identity, Depends(admin)]
```

- [ ] **Step 3: Write `preflight/bootstrap_entry.py`**

Replace `<PIN>` with the commit recorded in the prerequisite.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "preflight @ git+https://github.com/strider4560/preflight@<PIN>",
#   "python-hcl2>=8,<9",
# ]
# ///
"""Run by scripts/bootstrap.sh before it touches anything: the IDs are filled in, this shell
acts as the account's SSO administrator with the profile's region set, and the checkout holds
the latest main (a branch rebased onto it passes, for a rerun from a pull request's branch)."""

from preflight import Gate
from preflight.catalog import aws, git

import iac

gate = Gate("bootstrap_entry")
gate.include(iac.ids_filled)


@gate.guard("this shell is the account's administrator")
def administrator(identity: iac.Admin):
    return [aws.assumed(identity=identity), aws.region(identity=identity)]


@gate.guard("the checkout holds the latest main")
def current():
    return [git.up_to_date(remote="origin", branch="main")]


if __name__ == "__main__":
    gate.run()
```

- [ ] **Step 4: Write `preflight/bootstrap.py`**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "preflight @ git+https://github.com/strider4560/preflight@<PIN>",
#   "python-hcl2>=8,<9",
# ]
# ///
"""Bootstrap is applied: the parameters stacks/bootstrap publishes exist, and the stack plans
clean against the remote state, with no local state left by an interrupted first run."""

from preflight import Gate
from preflight.catalog import ssm, tofu

import iac

gate = Gate("bootstrap")
gate.include(iac.ids_filled)


@gate.guard("bootstrap published")
def published(env: iac.Env, identity: iac.Admin):
    return [
        ssm.parameters_exist(
            names=[
                "/platform/state/bucket",
                "/platform/state/kms_key_arn",
                "/platform/oidc/provider_arn",
            ],
            identity=identity,
        ),
        tofu.plan_clean(
            dir="stacks/bootstrap",
            identity=identity,
            var_files=[
                f"envs/{env}.tfvars",
                f"stacks/bootstrap/envs/{env}.tfvars",
                "stacks/bootstrap/envs/common.tfvars",
            ],
        ),
    ]


if __name__ == "__main__":
    gate.run()
```

Then: `chmod +x preflight/bootstrap_entry.py preflight/bootstrap.py`

- [ ] **Step 5: Validate both gates without credentials**

Run:
```bash
for env in dev prod; do
  PYTHONDONTWRITEBYTECODE=1 uv run --script preflight/bootstrap_entry.py "$env" --validate || break
  PYTHONDONTWRITEBYTECODE=1 uv run --script preflight/bootstrap.py "$env" --validate || break
done
```
Expected: four runs, each ending `Validated every guard; nothing was observed.`, exit 0.

Then check that validation catches a renamed key: `sed -i 's/^account_id /account_idx /' envs/dev.tfvars && uv run --script preflight/bootstrap.py dev --validate; echo "exit=$?"; git checkout -- envs/dev.tfvars`
Expected: `exit=2` and the problem line `guard 'bootstrap published': provider admin raised KeyError`. (Under `--validate`, `iac.filled` stands in as ok, so the run reaches `admin`, which cannot find `account_id`.)

- [ ] **Step 6: Point `scripts/bootstrap.sh` at the gate program**

Replace these two lines:

```bash
preflight() { uvx --from "git+https://github.com/strider4560/preflight@59e865c2b46d4d4739e15f4e167d9e40a87850b7" preflight "$@"; }
preflight check "$root/preflight/gates/bootstrap_entry.py" --contract "$root/preflight/contracts/$env.toml" || exit 1
```

with:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --script "$root/preflight/bootstrap_entry.py" "$env" || exit 1
```

and in the comment above them, change `(preflight/gates/bootstrap_entry.py)` to `(preflight/bootstrap_entry.py)`. Run `shellcheck scripts/bootstrap.sh` (expected: no output).

- [ ] **Step 7: Add the validation to `task check` and CI**

In `Taskfile.yml`, add after the `pin:check` task:

```yaml
  preflight:validate:
    desc: Check the preflight gates without credentials (nothing is observed)
    env: { PYTHONDONTWRITEBYTECODE: "1" }
    cmds:
      - for: [dev, prod]
        cmd: |
          uv run --script preflight/bootstrap_entry.py {{.ITEM}} --validate
          uv run --script preflight/bootstrap.py {{.ITEM}} --validate
```

and in the `check` task's `cmds`, add `- task: preflight:validate` after `- task: py:test`.

In `.github/workflows/platform.yml`, in the `validate` job, after the `Script tests` step, add:

```yaml
      - uses: astral-sh/setup-uv@v7
      - name: Preflight gates
        env:
          PYTHONDONTWRITEBYTECODE: "1"
        run: |
          for env in dev prod; do
            uv run --script preflight/bootstrap_entry.py "$env" --validate
            uv run --script preflight/bootstrap.py "$env" --validate
          done
```

Check the latest major of `astral-sh/setup-uv` first (`gh api repos/astral-sh/setup-uv/releases/latest --jq .tag_name`) and use its major in place of `v7`. Then pin it: `task py:setup && .venv/bin/python scripts/pin_actions.py .github/workflows`, and confirm `.venv/bin/python scripts/pin_actions.py --check .github/workflows` exits 0.

- [ ] **Step 8: Update the README**

In `README.md`'s layout table, replace the `preflight/` row with:

```markdown
| `preflight/` | Operator guardrails as [preflight](https://github.com/strider4560/preflight) gate programs: `bootstrap_entry.py`, which `scripts/bootstrap.sh` runs first, and `bootstrap.py`, which reports whether bootstrap is applied; `iac.py` holds what they share |
```

In "Bootstrapping an account", replace the sentence beginning ``The script first runs preflight's `bootstrap_entry` gate`` with:

```markdown
The script first runs `preflight/bootstrap_entry.py` (it needs `uv`), and stops, naming the next step, until the IDs are no longer placeholders, the shell acts as the account's `AWSAdministratorAccess` role with the profile's region set, and the checkout holds the latest `origin/main`.
```

and replace the sentence beginning ``uvx --from git+https://github.com/strider4560/preflight@<commit in scripts/bootstrap.sh> preflight status`` with:

```markdown
`uv run --script preflight/bootstrap.py <env>` reports whether bootstrap is applied in that account (parameters published, a clean plan, no leftover local state) and names the next step; `task preflight:validate` checks both gates without credentials.
```

- [ ] **Step 9: Live acceptance (controller, with the operator's SSO sessions)**

Run each and compare with the expected result (these are read-only; nothing is applied):

| Command | Expected |
|---|---|
| `AWS_PROFILE=sandbox uv run --script preflight/bootstrap_entry.py dev` | exit 0, `Every guard passed.` |
| `AWS_PROFILE=production uv run --script preflight/bootstrap_entry.py dev` | exit 1, stopped at "this shell is the account's administrator", `export AWS_PROFILE=sandbox` pasted |
| with `account_id = "000000000000"` in `envs/dev.tfvars` and `github_org_id = "0"` in `stacks/bootstrap/envs/common.tfvars` (restore with `git checkout --` afterwards): `uv run --script preflight/bootstrap_entry.py dev` | exit 1, stopped at "IDs filled in", `Fill account_id in envs/dev.tfvars.` and `Fill github_org_id in stacks/bootstrap/envs/common.tfvars.` with `gh api orgs/tellabsadmin --jq .id` |
| `uv run --script preflight/bootstrap.py dev` and `... prod` | exit 0 each, including `tofu.plan_clean(stacks/bootstrap)` |
| `AWS_PROFILE=production scripts/bootstrap.sh dev` | exit 1 at the gate, before any S3 or tofu call |
| `scripts/bootstrap.sh qa` | the usage line, exit 1 |

Record the results for the pull request description.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -F - <<'EOF'
feat(bootstrap): the preflight gates become programs

bootstrap_entry.py and bootstrap.py are PEP 723 scripts that read the
tfvars themselves and call preflight's checks with the values; the
contracts and their string tables are gone. task check and CI validate
both gates in both environments without credentials.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

Pushing this commit and updating PR #9's description are operator-approved steps.

---

### Task 9: Release

Operator approval is required for every step in this task.

- [ ] **Step 1:** After the preflight pull request for `feat/gate-programs` is reviewed and merged, tag the merge commit: `git -C ~/Develop/preflight tag -a v0.1.0 -m "preflight 0.1.0: gates as programs" <merge-sha> && git -C ~/Develop/preflight push origin v0.1.0`.
- [ ] **Step 2:** In iac, replace `@<PIN>` with `@v0.1.0` in both gates' headers, rerun `task preflight:validate`, and commit `chore(bootstrap): pin preflight v0.1.0`.
