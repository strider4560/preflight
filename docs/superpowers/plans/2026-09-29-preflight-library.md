# Preflight library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn this repository into `preflight`, an importable library and `uvx`-runnable CLI. A consumer's script calls `preflight check <gate.py> --contract <file>` and stops unless it exits 0; `preflight status` answers "where was I?".

**Architecture:**
- The runner resolves a TOML contract (literals and references into the consumer's own files), then builds a dependency graph of check instances. That graph includes implicit AWS sessions, SSM parameters and placeholders.
- Each instance runs in its own short-lived worker process, which observes through testinfra: a local host, and an Ansible host running `amazon.aws` and `community.aws` modules. DNS referrals are the exception and use dnspython directly.
- The runner prints an ordered worklist whose first open step is NEXT, and optionally writes JSON and JUnit. Preflight never runs the gated command and never mutates what it observes.

**Tech Stack:**
- Python ≥3.12, `hatchling`, `uv`/`uvx`;
- pydantic 2, pytest-testinfra 10, `ansible` (the full package), boto3, dnspython 2, python-hcl2 8, PyYAML;
- pytest and ruff for development.

**Spec:** `docs/superpowers/specs/2026-09-29-preflight-library-design.md`. Read it before starting any task; this plan argues from it.

## Global Constraints

- Python `>=3.12`; build backend `hatchling`; `src/` layout; the distribution and import package are both named `preflight`; the console script is `preflight = "preflight.cli:main"`.
- Runtime dependencies (exact bounds):
  - `ansible>=11`
  - `boto3>=1.40,<2`
  - `dnspython>=2.7,<3`
  - `pydantic>=2.11,<3`
  - `pytest-testinfra>=10,<11`
  - `python-hcl2>=8,<9`
  - `PyYAML>=6,<7`
- Development group: `pytest>=8.4,<10`, `ruff>=0.12,<1`.
- Ruff: `target-version = "py312"`, `line-length = 100`, `select = ["E", "F", "I", "B", "UP"]`. Every task ends with `uv run ruff check src tests` and `uv run ruff format --check src tests` passing.
- tfvars are parsed only with `SerializationOptions(strip_string_quotes=True, preserve_heredocs=False, with_comments=False)`. A value containing `${` is refused as an expression.
- `preflight check` exit codes: 0 every blocking item ok; 1 not; 2 invalid invocation, contract or gate (nothing observed); 3 internal error; 130 interrupted. `validate`: 0 or 2. `status`: 0 only when every milestone gate is satisfied in every environment, 1 otherwise, 2 or 3 as for `check`.
- Worker environment:
  - Removes `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_SECURITY_TOKEN`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME` and `AWS_WEB_IDENTITY_TOKEN_FILE`, except for `aws.assumed`.
  - Always removes `TF_CLI_ARGS`, `TF_WORKSPACE`, and every `TF_CLI_ARGS_*` and `TF_VAR_*`.
  - Sets `AWS_PROFILE` (except for `aws.assumed`), `AWS_REGION` and `AWS_DEFAULT_REGION` from the instance's identity.
  - Prepends the directory of preflight's own Python to `PATH`.
- Only a serialized `Outcome` leaves a worker. Exceptions are reduced to their type name; command output and exception messages never reach the terminal, JSON or JUnit unless a check puts a specific safe value in an item.
- The catalog never reads secret values. SSM is always read with `decrypt=False`.
- Consumer files are imported as the package `consumer` (`consumer.gates.<stem>`, `consumer.checks.<stem>`). Nothing is inserted into `sys.path`.
- JSON reports are written atomically with mode `0600`. JUnit never emits `<skipped>`.
- Default timeouts: 60 s per instance; `tofu.plan_clean` 600 s; at most 4 workers at once (`--jobs`).
- Commit messages follow Conventional Commits (`feat(core): …`, `feat(catalog): …`, `test: …`, `docs: …`, `chore: …`).

## Review Focus

1. **A gate file or a consumer check with a syntax or import error.** The operator expects exit 2 and a message naming the file, not a traceback or exit 3. Tested in Task 8 and Task 17.
2. **A contract with a TOML syntax error.** Expected: exit 2 with the line number. Tested in Task 7.
3. **Running from another working directory with relative paths** (`cd preflight && preflight check gates/x.py --contract contracts/dev.toml`). Expected: the same result as from the repo root. Tested in Task 17.
4. **Output piped to a file or a CI log.** Expected: no ANSI escape codes. Tested in Task 17.
5. **A worker that crashes, exits non-zero, or prints to stdout.** Expected: `error`, never `ok`. Tested in Task 14.

## Where this plan pins down the spec

The operator reviews these when approving the plan. Each is deliberate, not an oversight.

1. **Module names.** In the collections that ship with the `ansible` package, the ACM info module is `community.aws.acm_certificate_info` (the spec says `amazon.aws.acm_certificate_info`), and the SSM lookup is `amazon.aws.ssm_parameter` (the spec's `amazon.aws.aws_ssm` is its old alias). Task 1 confirms both.
2. **SecureString parameters are not refused; they are never decrypted.** No Ansible module or lookup reports a parameter's type. Every read uses `decrypt=False`, so a SecureString's plaintext never reaches preflight; its ciphertext simply fails the section's type check or comparison. The spec's "SecureString is an error" is therefore not implemented as a separate rule.
3. **The unused-key and unbound-section rules run only in `validate` and `status`.** `check` loads a single gate's closure, so it cannot know whether another gate uses a key; it applies neither rule. The spec says `check` "enforces it only for the gate it runs".
4. **`DelegationSection` has no `identity` field.** The lazy `ssm` reference for `name_servers` carries its own identity and brings in `aws.session` through `ssm.present`. The spec's illustrative contract shows `identity = "admin"` under `[delegation]`; under `validate` that key would be reported as unused.
5. **The JSON report adds `inputs_changed`** (a list of paths) beside the fields the spec shows.

## File Structure

```
pyproject.toml                         # replaced (Task 2)
README.md                              # rewritten (Task 25)
src/preflight/__init__.py              # public API re-exports, __version__
src/preflight/__main__.py              # python -m preflight
src/preflight/outcome.py               # Status, NextStep, Item, Outcome, ok/fail/pending/error, apply_remedy
src/preflight/check.py                 # Section, Remedy, IdentitySection, Requirement, Check, CheckInstance, @check, REGISTRY, field_problems
src/preflight/resolvers.py             # Reference, LazySsm, Resolver, parse_reference, apply_ssm_value
src/preflight/identity.py              # Identity, worker_environment
src/preflight/contract.py              # Contract, Placeholder, ContractError, load_contract, repo_root
src/preflight/gate.py                  # Gate, GateError, register_consumer, unload_consumer, load_gate_file, load_closure, load_directory
src/preflight/dnsclient.py             # DnsClient, Referral, DnsUnavailable, SystemTransport, norm
src/preflight/context.py               # Context, make_ansible_host
src/preflight/graph.py                 # Node, Plan, GraphError, build_plan
src/preflight/worker.py                # Job, execute, launch, kill_all, main
src/preflight/runner.py                # NodeResult, run_plan, default_launcher
src/preflight/render.py                # OpenStep, open_steps, render_check, render_status, to_json, write_json, to_junit
src/preflight/cli.py                   # main: check, status, validate
src/preflight/catalog/__init__.py
src/preflight/catalog/aws.py           # aws.session, aws.assumed, aws.region
src/preflight/catalog/ssm.py           # ssm.present, ssm.parameters
src/preflight/catalog/acm.py           # acm.issued
src/preflight/catalog/tofu.py          # tofu.plan_clean
src/preflight/catalog/dns.py           # dns.delegated, dns.undelegated, dns.cname, dns.caa
src/preflight/catalog/github.py        # github.auth, repo, variables, environments, ruleset, secret_names, workflow_green
src/preflight/catalog/git.py           # git.up_to_date
src/preflight/catalog/files.py         # files.present, absent, git_ignored, committed
src/preflight/catalog/sops.py          # sops.rule
tests/conftest.py                      # repo fixture, consumer cleanup
tests/fakes.py                         # FakeHost, FakeAnsibleHost, FakeDns, make_ctx, IDENTITY
tests/sample_checks.py                 # in-process checks for graph and runner tests
tests/test_*.py                        # one per module
```

Removed in Task 2: `gate/`, `checks/`, `contracts/prod.example.toml`, `examples/`, `run.py`, the old `tests/`, and `uv.lock`, which is regenerated. **Do not delete** `contracts/*.local.toml` or `reports/`: they are the operator's untracked, git-ignored files, and Task 26 may reuse the identity values in `contracts/sandbox.local.toml`.

---

### Task 1: Spike — retire the four risks (throwaway)

This task produces answers, not kept code. Work in the session scratchpad or a temp directory, never in `src/`.

**Files:**
- Create (throwaway): `$SCRATCH/spike/spike.py`, `$SCRATCH/spike/inventory.ini`
- Modify: this plan, section "Spike results" at the end

- [ ] **Step 1: Build a throwaway environment the way `uvx` will**

```bash
SCRATCH="$(mktemp -d)"; mkdir -p "$SCRATCH/spike" && cd "$SCRATCH/spike"
uv venv -q .venv && uv pip install -q --python .venv/bin/python 'ansible>=11' 'pytest-testinfra>=10,<11' 'boto3>=1.40,<2' 'python-hcl2>=8,<9'
.venv/bin/python -c "import importlib.metadata as m; print(m.version('ansible'), m.version('ansible-core'))"
ls .venv/lib/python3*/site-packages/ansible_collections/amazon/aws/plugins/lookup | grep -E 'ssm'
ls .venv/lib/python3*/site-packages/ansible_collections/community/aws/plugins/modules | grep -E 'acm_certificate_info'
```

Expected: versions print. `ssm_parameter.py` is listed under the amazon.aws lookups, and `acm_certificate_info.py` under the community.aws modules. Record the versions.

- [ ] **Step 2: Question 1 — testinfra's Ansible backend runs AWS info modules from this environment**

```python
# spike.py
import json, os, sys, pathlib
import testinfra

here = pathlib.Path(__file__).parent
inv = here / "inventory.ini"
inv.write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
os.environ["PATH"] = str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
host = testinfra.get_host("ansible://localhost", ansible_inventory=str(inv))
profile = os.environ.get("SPIKE_PROFILE", "sandbox")
for module, args in [
    ("amazon.aws.aws_caller_info", {"profile": profile, "region": "us-east-1"}),
    ("community.aws.acm_certificate_info", {"profile": profile, "region": "us-east-1"}),
]:
    try:
        result = host.ansible(module, json.dumps(args), check=True)
        print(module, "OK keys:", sorted(result))
    except Exception as exc:  # we want to know the shape, not the message
        print(module, "RAISED", type(exc).__name__, "keys:", sorted(getattr(exc, "result", {}) or {}))
```

Run: `cd "$SCRATCH/spike" && .venv/bin/python spike.py`

Expected: each module either prints `OK` with keys (including `account`/`arn` for `aws_caller_info`, and `certificates` for ACM), or raises `AnsibleException` whose result mentions credentials. A "module not found" or "couldn't resolve module" result means Question 1 failed. Run it once while logged in (`aws sso login --profile sandbox`) and once logged out.

- [ ] **Step 3: Question 2 — the SSM lookup through `ansible.builtin.debug`**

Append to `spike.py` and run again:

```python
expr = "{{ lookup('amazon.aws.ssm_parameter', '%s', decrypt=False, on_missing='skip', profile='%s', region='us-east-1') }}"
for name in ("/platform/state/bucket", "/preflight/spike/does-not-exist"):
    try:
        result = host.ansible("ansible.builtin.debug", json.dumps({"msg": expr % (name, profile)}), check=True)
        print(name, "msg repr:", repr(result.get("msg")))
    except Exception as exc:
        print(name, "RAISED", type(exc).__name__)
```

Expected: while logged in, the existing parameter returns its value, and the missing one returns `''` or `'None'`. Record the exact representation of "missing"; Task 10's `Context.ssm_lookup` treats `None`, `""` and `"None"` as missing, so adjust that tuple if the spike shows something else.

- [ ] **Step 4: Question 3 — killing a process group ends an `ansible` grandchild**

```python
# kill.py
import os, signal, subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c",
    "import subprocess; subprocess.run(['ansible','localhost','-m','ansible.builtin.command','-a','sleep 60'])"],
    start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env={**os.environ, "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"]})
time.sleep(5)
before = subprocess.run(["pgrep", "-g", str(p.pid)], capture_output=True, text=True).stdout.split()
os.killpg(p.pid, signal.SIGKILL); p.wait(); time.sleep(1)
after = subprocess.run(["pgrep", "-g", str(p.pid)], capture_output=True, text=True).stdout.split()
print("group before:", len(before), "after:", len(after))
```

Run: `.venv/bin/python kill.py`. Expected: `before` is 2 or more, `after` is 0. The same process-group semantics cover `tofu`, which does not start a new session.

- [ ] **Step 5: Question 4 — python-hcl2 on iac's real tfvars**

```bash
.venv/bin/python -c "
import hcl2
from hcl2 import SerializationOptions as S
o = S(strip_string_quotes=True, preserve_heredocs=False, with_comments=False)
print(hcl2.load(open('$HOME/Develop/tellabs/iac/envs/dev.tfvars'), serialization_options=o))"
```

Expected: plain values with no surrounding quotes, for example `{'env': 'dev', 'account_id': '711387098919', ...}`. This was already observed while planning with python-hcl2 8.1.2.

- [ ] **Step 6: Record the answers and adjust the plan**

Fill the "Spike results" section at the end of this plan: versions, the four answers, and the "missing" representation from Step 3. If Question 1 or 2 failed for a probe, write the fallback decision there; the spec allows the `aws` CLI with `--output json` through `ctx.host.run`. Then edit that probe's code in its task (Task 10 `Context.aws_module`/`ssm_lookup`, Tasks 11, 12, 19) before executing it.

```bash
git add docs/superpowers/plans/2026-09-29-preflight-library.md
git commit -m "docs(plan): record spike results"
```

---

### Task 2: Scaffold the package

**Files:**
- Delete: `gate/`, `checks/`, `contracts/prod.example.toml`, `examples/`, `run.py`, `tests/` (all tracked files in it), `uv.lock`
- Replace: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/preflight/__init__.py`, `src/preflight/catalog/__init__.py`, `tests/test_package.py`

**Interfaces:**
- Produces: the installable package `preflight` with `preflight.__version__ == "0.1.0"`.

- [ ] **Step 1: Remove the old scaffold (tracked files only)**

```bash
git rm -r -q gate checks examples tests run.py contracts/prod.example.toml uv.lock
git status --short   # contracts/*.local.toml and reports/ stay: untracked and ignored
```

- [ ] **Step 2: Write the new `pyproject.toml`**

```toml
[project]
name = "preflight"
version = "0.1.0"
description = "Operator guardrails: checks that stop a script and name the next step"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "ansible>=11",
  "boto3>=1.40,<2",
  "dnspython>=2.7,<3",
  "pydantic>=2.11,<3",
  "pytest-testinfra>=10,<11",
  "python-hcl2>=8,<9",
  "PyYAML>=6,<7",
]

[project.scripts]
preflight = "preflight.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/preflight"]

[dependency-groups]
dev = ["pytest>=8.4,<10", "ruff>=0.12,<1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

- [ ] **Step 3: Update `.gitignore`**

Replace its contents with:

```
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
dist/
reports/
contracts/*.local.toml
```

- [ ] **Step 4: Write the failing test**

```python
# tests/test_package.py
import importlib.metadata

import preflight


def test_version_matches_the_distribution():
    assert preflight.__version__ == importlib.metadata.version("preflight")
```

- [ ] **Step 5: Run it to see it fail**

Run: `uv sync && uv run pytest tests/test_package.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'preflight'`, or a missing `__version__`).

- [ ] **Step 6: Create the package**

```python
# src/preflight/__init__.py
"""Operator guardrails: checks that stop a script and name the next step."""

__version__ = "0.1.0"
```

```python
# src/preflight/catalog/__init__.py
"""Reusable checks. Import a module and call a check with a section name to bind it."""
```

- [ ] **Step 7: Run the test and lint**

Run: `uv sync && uv run pytest tests/test_package.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add -A pyproject.toml uv.lock .gitignore src tests
git commit -m "chore: replace the vendored scaffold with the preflight package skeleton"
```

---

### Task 3: Outcomes

**Files:**
- Create: `src/preflight/outcome.py`, `tests/test_outcome.py`

**Interfaces:**
- Produces:
  - `Status` (`StrEnum`: `OK="ok"`, `FAIL="fail"`, `PENDING="pending"`, `ERROR="error"`, `BLOCKED="blocked"`).
  - `NextStep(do: str, paste: str|None=None, wait: str|None=None, ref: str|None=None, generic: bool=False)` with `.to_dict()`.
  - `Item(key: str|None, status: Status, observed=None, next_step: NextStep|None=None, advisory=False, error_type: str|None=None)` with `.to_dict()` and `Item.from_dict()`.
  - `Outcome(items: tuple[Item, ...])` with `.status`, `.to_dict()` and `Outcome.from_dict()`.
  - Constructors:
    - `outcome(*items) -> Outcome`
    - `ok(key=None, *, observed=None, advisory=False) -> Item`
    - `fail(key=None, *, do, paste=None, wait=None, ref=None, observed=None, advisory=False, generic=False) -> Item`
    - `pending(key=None, *, wait, do="Nothing to do but wait.", paste=None, ref=None, observed=None, advisory=False) -> Item`
    - `error(key=None, *, do, paste=None, ref=None, error_type=None, observed=None, advisory=False, generic=False) -> Item`
  - `apply_remedy(result: Outcome, remedy: Mapping[str, str], values: Mapping[str, Any]) -> Outcome`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_outcome.py
import json

import pytest

from preflight.outcome import (
    Outcome,
    Status,
    apply_remedy,
    error,
    fail,
    ok,
    outcome,
    pending,
)


def test_status_is_the_worst_blocking_item():
    result = outcome(ok("a"), pending("b", wait="an hour"), fail("c", do="Fix c."))
    assert result.status is Status.FAIL


def test_error_outranks_fail_and_pending_outranks_ok():
    assert outcome(fail("a", do="x"), error("b", do="y")).status is Status.ERROR
    assert outcome(ok("a"), pending("b", wait="soon")).status is Status.PENDING


def test_advisory_items_never_block():
    assert outcome(ok("a"), fail("b", do="x", advisory=True)).status is Status.OK


def test_a_single_item_may_be_unnamed():
    assert outcome(ok()).items[0].key is None


@pytest.mark.parametrize("items", [(), (ok("a"), ok("a")), (ok(), ok("b"))])
def test_rejects_empty_duplicate_or_mixed_unnamed_items(items):
    with pytest.raises(ValueError):
        Outcome(items)


def test_round_trips_through_json():
    original = outcome(
        fail("app", do="Set NS.", paste="a NS b", wait="15 min", ref="README", observed=["x"]),
        error("b", do="Sign in.", error_type="Timeout", generic=True),
    )
    assert Outcome.from_dict(json.loads(json.dumps(original.to_dict()))) == original


def test_remedy_fills_empty_fields_and_replaces_only_a_generic_do():
    result = outcome(
        fail("a", do="The stack has changes.", generic=True),
        fail("b", do="Specific.", ref="own"),
    )
    merged = apply_remedy(
        result, {"do": "Rerun bootstrap.sh {environment}.", "ref": "README"}, {"environment": "dev"}
    )
    first, second = merged.items
    assert first.next_step.do == "Rerun bootstrap.sh dev."
    assert first.next_step.ref == "README"
    assert second.next_step.do == "Specific."
    assert second.next_step.ref == "own"


def test_remedy_leaves_ok_items_alone():
    merged = apply_remedy(outcome(ok("a")), {"do": "x"}, {})
    assert merged.items[0].next_step is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_outcome.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.outcome`).

- [ ] **Step 3: Implement**

```python
# src/preflight/outcome.py
"""What one observation produced: statuses, items, and the operator's next step."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    PENDING = "pending"
    ERROR = "error"
    BLOCKED = "blocked"


# Worst first: an outcome takes the worst status among its blocking items.
SEVERITY = (Status.ERROR, Status.FAIL, Status.PENDING, Status.OK)


@dataclass(frozen=True)
class NextStep:
    do: str
    paste: str | None = None
    wait: str | None = None
    ref: str | None = None
    # True when `do` is a default that the section's remedy may replace.
    generic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "do": self.do,
            "paste": self.paste,
            "wait": self.wait,
            "ref": self.ref,
            "generic": self.generic,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NextStep:
        return cls(
            data["do"], data.get("paste"), data.get("wait"), data.get("ref"), data.get("generic", False)
        )


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
    generic: bool = False,
) -> Item:
    return Item(key, Status.FAIL, observed, NextStep(do, paste, wait, ref, generic), advisory)


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
    generic: bool = False,
) -> Item:
    return Item(
        key, Status.ERROR, observed, NextStep(do, paste, None, ref, generic), advisory, error_type
    )


def apply_remedy(
    result: Outcome, remedy: Mapping[str, str], values: Mapping[str, Any]
) -> Outcome:
    """Merge a section's remedy over every non-ok item: `paste`, `wait` and `ref` fill fields the
    check left empty; `do` replaces the check's text only where the check marked it generic."""
    if not remedy:
        return result
    rendered = {name: text.format_map(values) for name, text in remedy.items() if text}
    items = []
    for item in result.items:
        step = item.next_step
        if item.status is Status.OK or step is None:
            items.append(item)
            continue
        changes = {
            name: rendered[name]
            for name in ("paste", "wait", "ref")
            if name in rendered and getattr(step, name) is None
        }
        if "do" in rendered and step.generic:
            changes["do"] = rendered["do"]
        items.append(replace(item, next_step=replace(step, **changes)))
    return Outcome(tuple(items))
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_outcome.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/outcome.py tests/test_outcome.py
git commit -m "feat(core): outcomes, items and next steps"
```

---

### Task 4: Checks and sections

**Files:**
- Create: `src/preflight/check.py`, `tests/test_check.py`
- Modify: `src/preflight/__init__.py`

**Interfaces:**
- Consumes: `Outcome` (Task 3).
- Produces:
  - `Name` / `IdentityRef` (`Annotated[str, pattern ^[a-z][a-z0-9_-]*$]`).
  - `Remedy` (pydantic: `do`, `paste`, `wait`, `ref`, all optional, extra forbidden).
  - `Section` (pydantic base; `extra="ignore"`, frozen; fields `region: str|None`, `timeout: float|None (>0)`, `remedy: Remedy`).
  - `IdentitySection(Section)` with `identity: IdentityRef`.
  - `Requirement(check_id: str, field: str)` and `session_for(field) -> Requirement`.
  - `Check(id, section, observe, binds, requires, timeout, ambient, module)`; calling `check(key)` returns `CheckInstance(check, key)`, whose `.id` is `"<check id>[<key>]"`.
  - `check(check_id, *, section=None, binds="section", requires=(), timeout=60.0, ambient=False)` (decorator).
  - `REGISTRY: dict[str, Check]`.
  - `field_problems(model, data, skip) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_check.py
import pytest

from preflight.check import (
    REGISTRY,
    IdentitySection,
    Section,
    check,
    field_problems,
    session_for,
)
from preflight.outcome import ok, outcome


class Things(Section):
    zones: list[str]
    root: str


@check("test.decorated", section=Things, requires=[session_for("identity")], timeout=5)
def decorated(ctx, s):
    return outcome(ok())


def test_the_decorator_registers_a_check_and_binds_instances():
    assert REGISTRY["test.decorated"] is decorated
    assert decorated("delegation").id == "test.decorated[delegation]"
    assert decorated.module == __name__
    assert decorated.timeout == 5
    assert decorated.requires[0].check_id == "aws.session"


def test_identity_bound_checks_use_the_identity_section():
    @check("test.identity_bound", binds="identity")
    def bound(ctx, s):
        return outcome(ok())

    assert bound.section is IdentitySection
    assert bound("admin").id == "test.identity_bound[admin]"


def test_redefining_an_id_in_another_function_is_refused():
    with pytest.raises(ValueError, match="already defined"):

        @check("test.decorated", section=Things)
        def other(ctx, s):
            return outcome(ok())


def test_a_section_bound_check_needs_a_model():
    with pytest.raises(ValueError, match="section model"):

        @check("test.no_model")
        def no_model(ctx, s):
            return outcome(ok())


def test_field_problems_validates_the_whole_model_when_nothing_is_skipped():
    assert field_problems(Things, {"zones": [1], "root": "x"}, set()) == [
        "zones.0: Input should be a valid string"
    ]
    assert field_problems(Things, {"zones": ["app"]}, set()) == ["root: Field required"]


def test_field_problems_leaves_skipped_fields_alone():
    assert field_problems(Things, {"zones": "lazy"}, {"zones"}) == ["root: Field required"]
    assert field_problems(Things, {"zones": "lazy", "root": "x"}, {"zones"}) == []


def test_unknown_keys_are_ignored_by_one_model():
    assert field_problems(Things, {"zones": [], "root": "x", "other": 1}, set()) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_check.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.check`).

- [ ] **Step 3: Implement**

```python
# src/preflight/check.py
"""Checks, their contract sections, and instances bound to a section or an identity."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from preflight.outcome import Outcome

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
IdentityRef = Name
Binds = Literal["section", "identity"]


class Remedy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    do: str | None = None
    paste: str | None = None
    wait: str | None = None
    ref: str | None = None


class Section(BaseModel):
    """Base of every check's contract section. One model ignores keys it does not declare; the
    graph refuses a key that no model bound to the section declares."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    region: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    remedy: Remedy = Remedy()


class IdentitySection(Section):
    identity: IdentityRef


@dataclass(frozen=True)
class Requirement:
    """An intrinsic prerequisite: the `check_id` instance for the alias in section `field`."""

    check_id: str
    field: str


def session_for(field: str) -> Requirement:
    return Requirement("aws.session", field)


@dataclass(frozen=True)
class Check:
    id: str
    section: type[Section]
    observe: Callable[[Any, Any], Outcome]
    binds: Binds = "section"
    requires: tuple[Requirement, ...] = ()
    timeout: float = 60.0
    # Run with the caller's AWS variables intact (only aws.assumed).
    ambient: bool = False
    module: str = ""

    def __call__(self, key: str) -> CheckInstance:
        return CheckInstance(self, key)


@dataclass(frozen=True)
class CheckInstance:
    check: Check
    key: str

    @property
    def id(self) -> str:
        return f"{self.check.id}[{self.key}]"


REGISTRY: dict[str, Check] = {}


def check(
    check_id: str,
    *,
    section: type[Section] | None = None,
    binds: Binds = "section",
    requires: tuple[Requirement, ...] | list[Requirement] = (),
    timeout: float = 60.0,
    ambient: bool = False,
) -> Callable[[Callable[[Any, Any], Outcome]], Check]:
    if binds == "identity":
        model = section or IdentitySection
        if not issubclass(model, IdentitySection):
            raise ValueError(f"{check_id}: an identity-bound check's model extends IdentitySection")
    elif section is None:
        raise ValueError(f"{check_id}: a section-bound check needs a section model")
    else:
        model = section

    def decorate(observe: Callable[[Any, Any], Outcome]) -> Check:
        new = Check(
            check_id, model, observe, binds, tuple(requires), timeout, ambient, observe.__module__
        )
        existing = REGISTRY.get(check_id)
        if existing is not None and (existing.module, existing.observe.__qualname__) != (
            new.module,
            observe.__qualname__,
        ):
            raise ValueError(f"check id {check_id!r} is already defined in {existing.module}")
        REGISTRY[check_id] = new
        return new

    return decorate


def _where(prefix: str, loc: tuple[Any, ...]) -> str:
    return ".".join([prefix, *map(str, loc)]) if prefix else ".".join(map(str, loc))


def field_problems(
    model: type[BaseModel], data: Mapping[str, Any], skip: set[str]
) -> list[str]:
    """Validate `data` against `model`, leaving out the fields in `skip` (placeholders and values
    not known until run time). Whole-model validators run only when nothing is skipped."""
    if not skip:
        try:
            model.model_validate(dict(data))
        except ValidationError as exc:
            return [f"{_where('', e['loc'])}: {e['msg']}" for e in exc.errors()]
        return []
    problems = []
    for name, info in model.model_fields.items():
        if name in skip:
            continue
        if name not in data:
            if info.is_required():
                problems.append(f"{name}: Field required")
            continue
        annotation = (
            Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        )
        try:
            TypeAdapter(annotation).validate_python(data[name])
        except ValidationError as exc:
            problems.extend(f"{_where(name, e['loc'])}: {e['msg']}" for e in exc.errors())
    return problems
```

Replace `src/preflight/__init__.py` with:

```python
"""Operator guardrails: checks that stop a script and name the next step."""

from preflight.check import (
    Check,
    CheckInstance,
    IdentityRef,
    IdentitySection,
    Name,
    Requirement,
    Section,
    check,
    session_for,
)
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

__version__ = "0.1.0"

__all__ = [
    "Check",
    "CheckInstance",
    "IdentityRef",
    "IdentitySection",
    "Item",
    "Name",
    "NextStep",
    "Outcome",
    "Requirement",
    "Section",
    "Status",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "session_for",
]
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_check.py tests/test_package.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/check.py src/preflight/__init__.py tests/test_check.py
git commit -m "feat(core): checks, sections and instances"
```

---

### Task 5: References and resolvers

**Files:**
- Create: `src/preflight/resolvers.py`, `tests/test_resolvers.py`

**Interfaces:**
- Produces:
  - `ResolveError(Exception)`.
  - `Reference(source, target, options: dict, placeholder: tuple, how: str|None)` with `.describe() -> str`.
  - `LazySsm(name, identity, format="text", path=None, how=None)` with `.to_dict()` and `LazySsm.from_dict()` (the encoding is `{"__lazy_ssm__": {...}}`).
  - `parse_reference(table) -> Reference | None`.
  - `dotted_get(value, path) -> Any`.
  - `Resolver(root)` with `.resolve(ref)` and `.inputs: dict[str, str]` (repo-relative path → sha256).
  - `apply_ssm_value(raw: str, lazy: LazySsm) -> Any`.
  - `SSM_NAME` (compiled pattern).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resolvers.py
import hashlib

import pytest

from preflight.resolvers import (
    LazySsm,
    ResolveError,
    Resolver,
    apply_ssm_value,
    parse_reference,
)

TFVARS = (
    'account_id = "111111111111"\n'
    'zones = ["app"]\n'
    'm = { "k" = "v", n = 1 }\n'
    "h = <<EOT\nline\nEOT\n"
    "# a comment\n"
)


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def resolve(root, table):
    return Resolver(root).resolve(parse_reference(table))


def test_tfvars_values_come_back_unquoted(tmp_path):
    write(tmp_path, "envs/dev.tfvars", TFVARS)
    get = lambda key: resolve(tmp_path, {"tfvars": "envs/dev.tfvars", "key": key})  # noqa: E731
    assert get("account_id") == "111111111111"
    assert get("zones") == ["app"]
    assert get("m") == {"k": "v", "n": 1}
    assert get("h") == "line"


@pytest.mark.parametrize("line", ['a = "${var.x}"\n', 'a = upper("x")\n'])
def test_tfvars_expressions_are_refused(tmp_path, line):
    write(tmp_path, "x.tfvars", line)
    with pytest.raises(ResolveError, match="expression"):
        resolve(tmp_path, {"tfvars": "x.tfvars", "key": "a"})


def test_missing_key_and_missing_file(tmp_path):
    write(tmp_path, "x.tfvars", 'a = "1"\n')
    with pytest.raises(ResolveError, match="has no variable b"):
        resolve(tmp_path, {"tfvars": "x.tfvars", "key": "b"})
    with pytest.raises(ResolveError, match="does not exist"):
        resolve(tmp_path, {"tfvars": "nope.tfvars", "key": "a"})


def test_yaml_and_json_paths(tmp_path):
    write(tmp_path, "a.yaml", "a:\n  b:\n    - x\n    - y\n")
    write(tmp_path, "a.json", '{"a": {"c": 2}}')
    assert resolve(tmp_path, {"yaml": "a.yaml", "path": "a.b.1"}) == "y"
    assert resolve(tmp_path, {"json": "a.json", "path": "a.c"}) == 2
    assert resolve(tmp_path, {"json": "a.json"}) == {"a": {"c": 2}}
    with pytest.raises(ResolveError, match="no 'z'"):
        resolve(tmp_path, {"yaml": "a.yaml", "path": "a.z"})


def test_yaml_glob_lists_matching_files_sorted(tmp_path):
    write(tmp_path, "apps/b.yaml", "name: b\n")
    write(tmp_path, "apps/a.yaml", "name: a\n")
    assert resolve(tmp_path, {"yaml_glob": "apps/*.yaml", "path": "name"}) == [
        {"file": "apps/a.yaml", "value": "a"},
        {"file": "apps/b.yaml", "value": "b"},
    ]


def test_ssm_references_are_lazy(tmp_path):
    lazy = resolve(
        tmp_path,
        {"ssm": "/platform/dns/name_servers", "identity": "admin", "format": "json", "path": "app"},
    )
    assert lazy == LazySsm("/platform/dns/name_servers", "admin", "json", "app", None)
    assert LazySsm.from_dict(lazy.to_dict()) == lazy
    assert apply_ssm_value('{"app": ["ns-1"]}', lazy) == ["ns-1"]
    with pytest.raises(ResolveError, match="not JSON"):
        apply_ssm_value("nope", lazy)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"tfvars": "x"}, "needs key"),
        ({"tfvars": "x", "key": "k", "yaml": "y"}, "more than one source"),
        ({"yaml": "x", "bogus": 1}, "unknown keys: bogus"),
        ({"ssm": "not-a-path", "identity": "admin"}, "parameter name"),
        ({"ssm": "/x"}, "needs identity"),
        ({"yaml": "x", "placeholder": "0"}, "placeholder must be a list"),
    ],
)
def test_malformed_references(table, message):
    with pytest.raises(ResolveError, match=message):
        parse_reference(table)


def test_a_table_without_a_source_is_not_a_reference():
    assert parse_reference({"k": 1}) is None


def test_inputs_record_what_was_read(tmp_path):
    path = write(tmp_path, "envs/dev.tfvars", TFVARS)
    resolver = Resolver(tmp_path)
    resolver.resolve(parse_reference({"tfvars": "envs/dev.tfvars", "key": "zones"}))
    assert resolver.inputs == {"envs/dev.tfvars": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_paths_outside_the_repository_are_refused(tmp_path):
    (tmp_path / "repo").mkdir()
    write(tmp_path, "outside.yaml", "a: 1\n")
    with pytest.raises(ResolveError, match="outside the repository"):
        resolve(tmp_path / "repo", {"yaml": "../outside.yaml"})
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_resolvers.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.resolvers`).

- [ ] **Step 3: Implement**

```python
# src/preflight/resolvers.py
"""References from a contract into the consumer's own files, and lazy SSM parameters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml
from hcl2 import SerializationOptions

SOURCES = ("tfvars", "yaml", "yaml_glob", "json", "ssm")
OPTIONS: dict[str, set[str]] = {
    "tfvars": {"key"},
    "yaml": {"path"},
    "yaml_glob": {"path"},
    "json": {"path"},
    "ssm": {"identity", "format", "path"},
}
COMMON = {"placeholder", "how"}
HCL_OPTIONS = SerializationOptions(
    strip_string_quotes=True, preserve_heredocs=False, with_comments=False
)
SSM_NAME = re.compile(r"^/[A-Za-z0-9_.\-/]+$")


class ResolveError(Exception):
    """A reference that cannot be resolved; the message names the problem."""


@dataclass(frozen=True)
class Reference:
    source: str
    target: str
    options: dict[str, Any]
    placeholder: tuple[Any, ...] = ()
    how: str | None = None

    def describe(self) -> str:
        if "key" in self.options:
            return f"{self.target} key {self.options['key']}"
        if self.options.get("path"):
            return f"{self.target} path {self.options['path']}"
        return self.target


@dataclass(frozen=True)
class LazySsm:
    name: str
    identity: str
    format: str = "text"
    path: str | None = None
    how: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "__lazy_ssm__": {
                "name": self.name,
                "identity": self.identity,
                "format": self.format,
                "path": self.path,
                "how": self.how,
            }
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LazySsm:
        return cls(**data["__lazy_ssm__"])


def parse_reference(table: Mapping[str, Any]) -> Reference | None:
    sources = [source for source in SOURCES if source in table]
    if not sources:
        return None
    if len(sources) > 1:
        raise ResolveError(f"a reference names more than one source: {', '.join(sources)}")
    source = sources[0]
    unknown = sorted(set(table) - OPTIONS[source] - COMMON - {source})
    if unknown:
        raise ResolveError(f"{source} reference has unknown keys: {', '.join(unknown)}")
    target = table[source]
    if not isinstance(target, str) or not target:
        raise ResolveError(f"{source} must be a non-empty string")
    if source == "tfvars" and not isinstance(table.get("key"), str):
        raise ResolveError("a tfvars reference needs key")
    if source == "ssm":
        if not SSM_NAME.match(target):
            raise ResolveError(f"{target!r} is not an SSM parameter name")
        if not isinstance(table.get("identity"), str):
            raise ResolveError("an ssm reference needs identity")
        if table.get("format", "text") not in ("text", "json"):
            raise ResolveError("format must be text or json")
    placeholder = table.get("placeholder", [])
    if not isinstance(placeholder, list):
        raise ResolveError("placeholder must be a list")
    how = table.get("how")
    if how is not None and not isinstance(how, str):
        raise ResolveError("how must be a string")
    options = {name: table[name] for name in OPTIONS[source] if name in table}
    return Reference(source, target, options, tuple(placeholder), how)


def dotted_get(value: Any, path: str | None) -> Any:
    if not path:
        return value
    for segment in path.split("."):
        if isinstance(value, list) and segment.isdigit():
            index = int(segment)
            if index >= len(value):
                raise ResolveError(f"index {segment} is out of range")
            value = value[index]
        elif isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            raise ResolveError(f"no {segment!r} at this path")
    return value


def _reject_expressions(value: Any, ref: Reference) -> None:
    if isinstance(value, str) and "${" in value:
        raise ResolveError(f"{ref.describe()} is an expression, not a literal")
    if isinstance(value, list):
        for element in value:
            _reject_expressions(element, ref)
    if isinstance(value, dict):
        for element in value.values():
            _reject_expressions(element, ref)


def apply_ssm_value(raw: str, lazy: LazySsm) -> Any:
    if lazy.format == "text":
        return dotted_get(raw, lazy.path) if lazy.path else raw
    try:
        value = json.loads(raw)
    except ValueError:
        raise ResolveError(f"{lazy.name} is not JSON") from None
    return dotted_get(value, lazy.path)


class Resolver:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.inputs: dict[str, str] = {}
        self._cache: dict[Path, Any] = {}

    def resolve(self, ref: Reference) -> Any:
        if ref.source == "ssm":
            return LazySsm(
                ref.target,
                ref.options["identity"],
                ref.options.get("format", "text"),
                ref.options.get("path"),
                ref.how,
            )
        if ref.source == "yaml_glob":
            return [
                {
                    "file": path.relative_to(self.root).as_posix(),
                    "value": dotted_get(self._load(path, "yaml"), ref.options.get("path")),
                }
                for path in sorted(p.resolve() for p in self.root.glob(ref.target) if p.is_file())
            ]
        document = self._load(self._path(ref.target), ref.source)
        if ref.source == "tfvars":
            key = ref.options["key"]
            if key not in document:
                raise ResolveError(f"{ref.target} has no variable {key}")
            value = document[key]
            _reject_expressions(value, ref)
            return value
        return dotted_get(document, ref.options.get("path"))

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ResolveError(f"{relative} is outside the repository")
        if not path.is_file():
            raise ResolveError(f"{relative} does not exist")
        return path

    def _load(self, path: Path, kind: str) -> Any:
        if path not in self._cache:
            raw = path.read_bytes()
            relative = path.relative_to(self.root).as_posix()
            self.inputs[relative] = hashlib.sha256(raw).hexdigest()
            try:
                text = raw.decode("utf-8")
                if kind == "tfvars":
                    data = hcl2.loads(text, serialization_options=HCL_OPTIONS)
                elif kind == "json":
                    data = json.loads(text)
                else:
                    data = yaml.safe_load(text)
            except Exception as exc:
                raise ResolveError(f"{relative} cannot be parsed ({type(exc).__name__})") from None
            self._cache[path] = data
        return self._cache[path]
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_resolvers.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/resolvers.py tests/test_resolvers.py
git commit -m "feat(core): contract references into tfvars, yaml, json and lazy ssm"
```

---

### Task 6: Identities and the worker environment

**Files:**
- Create: `src/preflight/identity.py`, `tests/test_identity.py`

**Interfaces:**
- Produces:
  - `Identity` (pydantic: `profile`, `region`, `account_id`, and exactly one of `role` / `permission_set`), with `.role_matches(name) -> bool`, `.matches(account, arn) -> bool` and `.describe() -> str`.
  - `AWS_VARIABLES` (tuple).
  - `worker_environment(base: Mapping[str,str], identity: Identity|None, *, keep_aws: bool, bin_dir: str) -> dict[str,str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_identity.py
import pytest
from pydantic import ValidationError

from preflight.identity import Identity, worker_environment

SPEC = {
    "profile": "sandbox",
    "region": "us-east-1",
    "account_id": "111111111111",
    "permission_set": "AWSAdministratorAccess",
}
ARN = (
    "arn:aws:sts::111111111111:assumed-role/"
    "AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d/operator@example.com"
)


def test_a_permission_set_matches_only_its_generated_role_names():
    identity = Identity(**SPEC)
    assert identity.role_matches("AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d")
    assert not identity.role_matches(
        "AWSReservedSSO_AWSAdministratorAccess_ReadOnly_bd9c3ff84c4cd64d"
    )
    assert not identity.role_matches("AWSReservedSSO_AWSAdministratorAccess_x")


def test_matches_an_assumed_role_in_the_expected_account_only():
    identity = Identity(**SPEC)
    assert identity.matches("111111111111", ARN)
    assert not identity.matches("222222222222", ARN.replace("111111111111", "222222222222"))
    assert not identity.matches("111111111111", "arn:aws:iam::111111111111:user/operator")


def test_an_exact_role_name():
    spec = {**SPEC, "role": "Deploy"}
    del spec["permission_set"]
    identity = Identity(**spec)
    assert identity.role_matches("Deploy")
    assert not identity.role_matches("Deploy2")
    assert identity.describe() == "role Deploy in account 111111111111"


@pytest.mark.parametrize("change", [{"role": "Deploy"}, {"permission_set": None}])
def test_exactly_one_of_role_or_permission_set(change):
    with pytest.raises(ValidationError, match="exactly one of role or permission_set"):
        Identity(**{**SPEC, **change})


def test_the_worker_environment_drops_credentials_and_tofu_overrides():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/op",
        "AWS_ACCESS_KEY_ID": "key",
        "AWS_SESSION_TOKEN": "token",
        "AWS_PROFILE": "production",
        "TF_CLI_ARGS_plan": "-target=x",
        "TF_VAR_x": "1",
        "TF_WORKSPACE": "w",
        "TF_PLUGIN_CACHE_DIR": "/cache",
    }
    env = worker_environment(base, Identity(**SPEC), keep_aws=False, bin_dir="/venv/bin")
    assert env == {
        "PATH": "/venv/bin:/usr/bin",
        "HOME": "/home/op",
        "TF_PLUGIN_CACHE_DIR": "/cache",
        "AWS_PROFILE": "sandbox",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


def test_aws_assumed_keeps_the_callers_credentials():
    base = {"PATH": "/usr/bin", "AWS_ACCESS_KEY_ID": "key", "AWS_PROFILE": "production"}
    env = worker_environment(base, Identity(**SPEC), keep_aws=True, bin_dir="/venv/bin")
    assert env["AWS_ACCESS_KEY_ID"] == "key"
    assert env["AWS_PROFILE"] == "production"
    assert env["AWS_REGION"] == "us-east-1"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_identity.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.identity`).

- [ ] **Step 3: Implement**

```python
# src/preflight/identity.py
"""Expected AWS identities, how an observed caller is matched, and each worker's environment."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

AccountId = Annotated[str, StringConstraints(pattern=r"^\d{12}$")]
Profile = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.+=,@-]+$")]
Region = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")]
RoleName = Annotated[str, StringConstraints(pattern=r"^[\w+=,.@-]{1,64}$")]
PermissionSet = Annotated[str, StringConstraints(pattern=r"^[\w+=,.@-]{1,32}$")]

AWS_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_ROLE_SESSION_NAME",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)
TOFU_VARIABLES = ("TF_CLI_ARGS", "TF_WORKSPACE")
TOFU_PREFIXES = ("TF_CLI_ARGS_", "TF_VAR_")
ASSUMED_ROLE = re.compile(r"^arn:aws[a-z-]*:sts::(\d{12}):assumed-role/([^/]+)/.+$")


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    profile: Profile
    region: Region
    account_id: AccountId
    role: RoleName | None = None
    permission_set: PermissionSet | None = None

    @model_validator(mode="after")
    def one_role(self) -> Self:
        if (self.role is None) == (self.permission_set is None):
            raise ValueError("give exactly one of role or permission_set")
        return self

    def role_matches(self, role_name: str) -> bool:
        if self.role is not None:
            return role_name == self.role
        pattern = rf"AWSReservedSSO_{re.escape(self.permission_set or '')}_[0-9a-f]{{16}}"
        return re.fullmatch(pattern, role_name) is not None

    def matches(self, account: str, arn: str) -> bool:
        match = ASSUMED_ROLE.match(arn)
        return (
            match is not None
            and account == self.account_id
            and match[1] == self.account_id
            and self.role_matches(match[2])
        )

    def describe(self) -> str:
        role = self.role or f"AWSReservedSSO_{self.permission_set}_*"
        return f"role {role} in account {self.account_id}"


def worker_environment(
    base: Mapping[str, str], identity: Identity | None, *, keep_aws: bool, bin_dir: str
) -> dict[str, str]:
    env = dict(base)
    if not keep_aws:
        for name in AWS_VARIABLES:
            env.pop(name, None)
    for name in list(env):
        if name in TOFU_VARIABLES or name.startswith(TOFU_PREFIXES):
            del env[name]
    if identity is not None:
        if not keep_aws:
            env["AWS_PROFILE"] = identity.profile
        env["AWS_REGION"] = identity.region
        env["AWS_DEFAULT_REGION"] = identity.region
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_identity.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/identity.py tests/test_identity.py
git commit -m "feat(core): identities and the scrubbed worker environment"
```

---

### Task 7: Contracts

**Files:**
- Create: `src/preflight/contract.py`, `tests/conftest.py`, `tests/test_contract.py`

**Interfaces:**
- Consumes:
  - `field_problems` (Task 4);
  - `Identity` (Task 6);
  - `NextStep` (Task 3);
  - `LazySsm`, `Reference`, `ResolveError`, `Resolver` and `parse_reference` (Task 5).
- Produces:
  - `ContractError(path, problems)` with `.problems: list[str]`.
  - `Placeholder(path, owner, field, reference)` with `.id` and `.next_step() -> NextStep`.
  - `Contract`, with fields `path`, `root`, `scope`, `environment`, `identity_data`, `sections`, `placeholders` and `inputs`, and methods `.identity(alias)`, `.ready_identities()`, `.placeholder_fields(owner)`, `.lazy_fields(section)`, `.template_values(section)` and `.changed_inputs()`.
  - `repo_root(start) -> Path` and `load_contract(path) -> Contract`.
- Test helpers (in `tests/conftest.py`), used by later tasks: the fixture `repo` (a fresh git work tree) and the function `write(root, relative, text) -> Path`.

- [ ] **Step 1: Create the shared test helpers**

```python
# tests/conftest.py
import subprocess
from pathlib import Path

import pytest


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path.resolve()
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_contract.py
import pytest
from conftest import write

from preflight.contract import ContractError, load_contract
from preflight.resolvers import LazySsm

TFVARS = 'account_id = "111111111111"\nroot_domain = "tellabs.dev"\nzones = ["app"]\n'
CONTRACT = """
schema_version = 1
environment = "dev"

[identities.admin]
profile = "sandbox"
region = "us-east-1"
account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID" }
permission_set = "AWSAdministratorAccess"

[delegation]
root = { tfvars = "envs/dev.tfvars", key = "root_domain" }
zones = { tfvars = "envs/dev.tfvars", key = "zones", placeholder = ["CHANGEME"] }
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json" }

[delegation.remedy]
ref = "README, DNS step 1"
"""


def setup(repo, tfvars=TFVARS, contract=CONTRACT):
    write(repo, "envs/dev.tfvars", tfvars)
    return write(repo, "preflight/contracts/dev.toml", contract)


def test_loads_and_resolves(repo):
    contract = load_contract(setup(repo))
    assert (contract.scope, contract.environment, contract.root) == ("environment", "dev", repo)
    assert contract.identity("admin").account_id == "111111111111"
    delegation = contract.sections["delegation"]
    assert delegation["root"] == "tellabs.dev"
    assert delegation["zones"] == ["app"]
    assert isinstance(delegation["name_servers"], LazySsm)
    assert delegation["remedy"] == {"ref": "README, DNS step 1"}
    assert contract.placeholders == []
    assert set(contract.inputs) == {"preflight/contracts/dev.toml", "envs/dev.tfvars"}
    assert contract.lazy_fields("delegation") == {"name_servers"}
    assert contract.template_values("delegation") == {"root": "tellabs.dev", "environment": "dev"}


def test_placeholders_are_recorded_not_rejected(repo):
    tfvars = 'account_id = "000000000000"\nroot_domain = "tellabs.dev"\nzones = ["app", "CHANGEME"]\n'
    contract = load_contract(setup(repo, tfvars=tfvars))
    assert [(p.path, p.owner, p.field) for p in contract.placeholders] == [
        ("identities.admin.account_id", "identities.admin", "account_id"),
        ("delegation.zones[1]", "delegation", "zones"),
    ]
    first = contract.placeholders[0]
    assert first.id == "contract.placeholder[identities.admin.account_id]"
    assert first.next_step().do == "Fill `account_id` in `envs/dev.tfvars`."
    assert first.next_step().paste == "the Sandbox account ID"
    assert contract.ready_identities() == {}


def test_every_problem_is_reported_together(repo):
    bad = (
        CONTRACT.replace("schema_version = 1", "schema_version = 2")
        .replace('key = "root_domain"', 'key = "nope"')
        .replace('permission_set = "AWSAdministratorAccess"', 'permission_set = "AWSAdministratorAccess"\nrole = "x"')
    )
    with pytest.raises(ContractError) as caught:
        load_contract(setup(repo, contract=bad))
    text = "\n".join(caught.value.problems)
    assert "schema_version must be 1" in text
    assert "has no variable nope" in text
    assert "exactly one of role or permission_set" in text


def test_a_toml_syntax_error_names_the_line(repo):
    with pytest.raises(ContractError) as caught:
        load_contract(setup(repo, contract="schema_version = \n"))
    assert "not valid TOML" in caught.value.problems[0]
    assert "line 1" in caught.value.problems[0]


def test_a_contract_outside_a_git_work_tree_is_refused(tmp_path):
    path = write(tmp_path, "c.toml", 'schema_version = 1\nenvironment = "dev"\n')
    with pytest.raises(ContractError, match="not inside a git work tree"):
        load_contract(path)


def test_an_identity_cannot_use_ssm(repo):
    bad = CONTRACT.replace(
        'account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID" }',
        'account_id = { ssm = "/x", identity = "admin" }',
    )
    with pytest.raises(ContractError, match="cannot use an ssm reference"):
        load_contract(setup(repo, contract=bad))


def test_a_repository_contract_has_no_environment(repo):
    with pytest.raises(ContractError, match="has no environment"):
        load_contract(
            setup(repo, contract='schema_version = 1\nscope = "repository"\nenvironment = "dev"\n')
        )


def test_top_level_scalars_other_than_the_known_keys_are_refused(repo):
    with pytest.raises(ContractError, match="unknown top-level key"):
        load_contract(setup(repo, contract='schema_version = 1\nenvironment = "dev"\nextra = 1\n'))


def test_changed_inputs(repo):
    contract = load_contract(setup(repo))
    (repo / "envs/dev.tfvars").write_text(TFVARS + 'extra = "1"\n')
    assert contract.changed_inputs() == ["envs/dev.tfvars"]
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_contract.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.contract`).

- [ ] **Step 4: Implement**

```python
# src/preflight/contract.py
"""Contracts: expected values as literals or references, checked before anything is observed."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from preflight.check import field_problems
from preflight.identity import Identity
from preflight.outcome import NextStep
from preflight.resolvers import LazySsm, Reference, ResolveError, Resolver, parse_reference

NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
TOP_LEVEL = {"schema_version", "scope", "environment", "identities"}
SCOPES = ("environment", "repository")


class ContractError(Exception):
    """The contract cannot be used; `problems` lists everything wrong with it."""

    def __init__(self, path: Path, problems: list[str]):
        self.path = path
        self.problems = list(problems)
        super().__init__(f"{path}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class Placeholder:
    path: str
    owner: str
    field: str
    reference: Reference

    @property
    def id(self) -> str:
        return f"contract.placeholder[{self.path}]"

    def next_step(self) -> NextStep:
        options = self.reference.options
        key = options.get("key") or options.get("path") or self.field
        return NextStep(do=f"Fill `{key}` in `{self.reference.target}`.", paste=self.reference.how)


def _contains_lazy(value: Any) -> bool:
    if isinstance(value, LazySsm):
        return True
    if isinstance(value, dict):
        return any(_contains_lazy(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_lazy(v) for v in value)
    return False


@dataclass
class Contract:
    path: Path
    root: Path
    scope: str
    environment: str | None
    identity_data: dict[str, dict[str, Any]]
    sections: dict[str, dict[str, Any]]
    placeholders: list[Placeholder]
    inputs: dict[str, str]

    def identity(self, alias: str) -> Identity:
        return Identity.model_validate(self.identity_data[alias])

    def ready_identities(self) -> dict[str, Identity]:
        blocked = {p.owner for p in self.placeholders}
        return {
            alias: self.identity(alias)
            for alias in self.identity_data
            if f"identities.{alias}" not in blocked
        }

    def placeholder_fields(self, owner: str) -> set[str]:
        return {p.field for p in self.placeholders if p.owner == owner}

    def lazy_fields(self, section: str) -> set[str]:
        return {name for name, value in self.sections[section].items() if _contains_lazy(value)}

    def template_values(self, section: str) -> dict[str, Any]:
        skip = self.placeholder_fields(section) | self.lazy_fields(section)
        values = {
            name: value
            for name, value in self.sections[section].items()
            if name not in skip and isinstance(value, (str, int, float, bool))
        }
        values["environment"] = self.environment or ""
        return values

    def changed_inputs(self) -> list[str]:
        changed = []
        for relative, digest in self.inputs.items():
            try:
                current = hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
            except OSError:
                current = None
            if current != digest:
                changed.append(relative)
        return changed


def repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ContractError(start, ["not inside a git work tree"])
    return Path(result.stdout.strip()).resolve()


class _Loader:
    def __init__(self, resolver: Resolver):
        self.resolver = resolver
        self.placeholders: list[Placeholder] = []
        self.problems: list[str] = []

    def table(self, table: dict[str, Any], path: str, owner: str) -> dict[str, Any]:
        return {name: self.value(v, f"{path}.{name}", owner, name) for name, v in table.items()}

    def value(self, value: Any, path: str, owner: str, field: str) -> Any:
        if isinstance(value, dict):
            try:
                ref = parse_reference(value)
            except ResolveError as exc:
                self.problems.append(f"{path}: {exc}")
                return None
            if ref is None:
                return {k: self.value(v, f"{path}.{k}", owner, field) for k, v in value.items()}
            try:
                resolved = self.resolver.resolve(ref)
            except ResolveError as exc:
                self.problems.append(f"{path}: {exc}")
                return None
            if not isinstance(resolved, LazySsm):
                self.find_placeholders(resolved, path, owner, field, ref)
            return resolved
        if isinstance(value, list):
            return [self.value(v, f"{path}[{i}]", owner, field) for i, v in enumerate(value)]
        return value

    def find_placeholders(
        self, value: Any, path: str, owner: str, field: str, ref: Reference
    ) -> None:
        if isinstance(value, list):
            for i, element in enumerate(value):
                self.find_placeholders(element, f"{path}[{i}]", owner, field, ref)
        elif isinstance(value, dict):
            for key, element in value.items():
                self.find_placeholders(element, f"{path}.{key}", owner, field, ref)
        elif any(value == p and type(value) is type(p) for p in ref.placeholder):
            self.placeholders.append(Placeholder(path, owner, field, ref))


def load_contract(path: Path) -> Contract:
    path = path.resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(path, [f"cannot be read ({exc.strerror})"]) from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ContractError(path, [f"not valid TOML: {exc}"]) from None
    root = repo_root(path.parent)
    resolver = Resolver(root)
    resolver.inputs[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    loader = _Loader(resolver)
    problems = loader.problems

    if data.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    scope = data.get("scope", "environment")
    environment = data.get("environment")
    if scope not in SCOPES:
        problems.append("scope must be environment or repository")
    elif scope == "environment" and not (isinstance(environment, str) and NAME.match(environment)):
        problems.append("environment is required, lowercase letters, digits, - and _")
    elif scope == "repository" and environment is not None:
        problems.append("a repository contract has no environment")
    for key, value in data.items():
        if key not in TOP_LEVEL and not isinstance(value, dict):
            problems.append(f"{key}: unknown top-level key")

    identity_data: dict[str, dict[str, Any]] = {}
    identities = data.get("identities", {})
    if not isinstance(identities, dict):
        problems.append("identities must be a table")
        identities = {}
    for alias, spec in identities.items():
        where = f"identities.{alias}"
        if not NAME.match(alias) or not isinstance(spec, dict):
            problems.append(f"{where}: an identity is a table named in lowercase")
            continue
        resolved = loader.table(spec, where, where)
        if any(_contains_lazy(v) for v in resolved.values()):
            problems.append(f"{where}: an identity cannot use an ssm reference")
            continue
        skip = {p.field for p in loader.placeholders if p.owner == where}
        problems.extend(f"{where}.{p}" for p in field_problems(Identity, resolved, skip))
        identity_data[alias] = resolved

    sections: dict[str, dict[str, Any]] = {}
    for name, table in data.items():
        if name in TOP_LEVEL or not isinstance(table, dict):
            continue
        if not NAME.match(name):
            problems.append(f"[{name}]: section names are lowercase letters, digits, - and _")
            continue
        sections[name] = loader.table(table, name, name)
        if isinstance(sections[name].get("identity"), LazySsm):
            problems.append(f"{name}.identity: cannot be an ssm reference")
        for value in sections[name].values():
            for lazy in _lazy_values(value):
                if lazy.identity not in identities:
                    problems.append(f"[{name}]: ssm {lazy.name} names unknown identity {lazy.identity}")

    if problems:
        raise ContractError(path, problems)
    return Contract(
        path=path,
        root=root,
        scope=scope,
        environment=environment if scope == "environment" else None,
        identity_data=identity_data,
        sections=sections,
        placeholders=loader.placeholders,
        inputs=resolver.inputs,
    )


def _lazy_values(value: Any):
    if isinstance(value, LazySsm):
        yield value
    elif isinstance(value, dict):
        for element in value.values():
            yield from _lazy_values(element)
    elif isinstance(value, list):
        for element in value:
            yield from _lazy_values(element)
```

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_contract.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. If ruff flags the long contract line in the test module (E501), add `# noqa: E501` at the end of that line; the string must stay on one line because it's TOML.

- [ ] **Step 6: Commit**

```bash
git add src/preflight/contract.py tests/conftest.py tests/test_contract.py
git commit -m "feat(core): contracts with references, placeholders and collected problems"
```

---

### Task 8: Gates and consumer loading

**Files:**
- Create: `src/preflight/gate.py`, `tests/test_gate.py`
- Modify: `src/preflight/__init__.py` (export `Gate`), `tests/conftest.py` (autouse consumer cleanup)

**Interfaces:**
- Consumes: `CheckInstance` (Task 4).
- Produces:
  - `Gate(name, checks, requires=(), guards=None, scope="environment")`, with `.milestone`.
  - `GateError(problems)`.
  - `register_consumer(consumer_dir)` and `unload_consumer()`.
  - `load_gate_file(path) -> Gate`, `load_closure(path) -> list[Gate]` (required gates first) and `load_directory(gates_dir) -> list[Gate]`, which are topologically ordered with ties broken by name.
  - `order_gates(gates) -> list[Gate]`.

- [ ] **Step 1: Add consumer cleanup to `tests/conftest.py`**

Append:

```python
from preflight.gate import unload_consumer  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_consumer():
    unload_consumer()
    yield
    unload_consumer()
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_gate.py
import pytest
from conftest import write

from preflight.gate import Gate, GateError, load_closure, load_directory, load_gate_file

CHECK = """
from preflight import Section, check, ok, outcome


class Things(Section):
    items: list[str] = []


@check("sample.thing", section=Things)
def thing(ctx, s):
    return outcome(ok())
"""

GATE = """
from preflight import Gate
from consumer.checks.sample import thing

gate = Gate("{name}", checks=[thing("things")], requires={requires!r})
"""


def consumer(repo, gates):
    write(repo, "preflight/checks/sample.py", CHECK)
    for name, requires in gates.items():
        write(repo, f"preflight/gates/{name}.py", GATE.format(name=name, requires=requires))
    return repo / "preflight" / "gates"


def test_loads_a_gate_and_its_required_gates_in_order(repo):
    gates = consumer(repo, {"a": [], "b": ["a"], "c": ["b"]})
    assert [g.name for g in load_closure(gates / "c.py")] == ["a", "b", "c"]


def test_a_gate_is_named_after_its_file(repo):
    gates = consumer(repo, {"a": []})
    (gates / "x.py").write_text(GATE.format(name="y", requires=[]))
    with pytest.raises(GateError, match="name it 'x'"):
        load_gate_file(gates / "x.py")


def test_a_missing_required_gate(repo):
    gates = consumer(repo, {"b": ["a"]})
    with pytest.raises(GateError, match="a.py does not exist"):
        load_closure(gates / "b.py")


def test_a_cycle_between_gates(repo):
    gates = consumer(repo, {"a": ["b"], "b": ["a"]})
    with pytest.raises(GateError, match="cycle"):
        load_directory(gates)


def test_a_syntax_error_names_the_file(repo):
    gates = consumer(repo, {"a": []})
    (gates / "broken.py").write_text("gate = (\n")
    with pytest.raises(GateError) as caught:
        load_gate_file(gates / "broken.py")
    assert "broken.py" in caught.value.problems[0]
    assert "SyntaxError" in caught.value.problems[0]


def test_a_gate_named_like_a_stdlib_module_does_not_shadow_it(repo):
    gates = consumer(repo, {"secrets": []})
    load_gate_file(gates / "secrets.py")
    import secrets

    assert hasattr(secrets, "token_hex")


def test_load_directory_orders_by_requires_and_skips_private_files(repo):
    gates = consumer(repo, {"b": ["a"], "a": []})
    (gates / "_helpers.py").write_text("x = 1\n")
    assert [g.name for g in load_directory(gates)] == ["a", "b"]


def test_a_gate_needs_check_instances():
    with pytest.raises(GateError, match="has no checks"):
        Gate("x", checks=[])


def test_a_bare_check_is_refused(repo):
    consumer(repo, {"a": []})
    from preflight.gate import register_consumer

    register_consumer(repo / "preflight")
    from consumer.checks.sample import thing

    with pytest.raises(GateError, match="call the check with a section name"):
        Gate("x", checks=[thing])
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_gate.py -v`
Expected: FAIL (`ImportError` from `tests/conftest.py`: `preflight.gate` does not exist).

- [ ] **Step 4: Implement**

```python
# src/preflight/gate.py
"""Gates: named sets of check instances in the consumer's repository, loaded by file path.

The consumer directory (the parent of `gates/`) is imported as the package `consumer`, so a gate
imports repo-local checks as `consumer.checks.<file>`. Nothing is added to `sys.path`."""

from __future__ import annotations

import importlib
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from preflight.check import CheckInstance

STEM = re.compile(r"^[a-z][a-z0-9_]*$")


class GateError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Gate:
    name: str
    checks: tuple[CheckInstance, ...]
    requires: tuple[str, ...] = ()
    guards: str | None = None
    scope: Literal["environment", "repository"] = "environment"

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "requires", tuple(self.requires))
        if not self.checks:
            raise GateError([f"gate {self.name} has no checks"])
        for instance in self.checks:
            if not isinstance(instance, CheckInstance):
                raise GateError(
                    [f"gate {self.name}: {instance!r} is not bound; call the check with a section name"]
                )
        if self.scope not in ("environment", "repository"):
            raise GateError([f"gate {self.name}: scope must be environment or repository"])

    @property
    def milestone(self) -> bool:
        return self.guards is None


def register_consumer(consumer_dir: Path) -> None:
    consumer_dir = consumer_dir.resolve()
    existing = sys.modules.get("consumer")
    if existing is not None:
        if list(getattr(existing, "__path__", [])) == [str(consumer_dir)]:
            return
        raise GateError([f"another consumer directory is already loaded: {existing.__path__[0]}"])
    package = types.ModuleType("consumer")
    package.__path__ = [str(consumer_dir)]
    package.__package__ = "consumer"
    sys.modules["consumer"] = package
    importlib.invalidate_caches()


def unload_consumer() -> None:
    for name in [n for n in sys.modules if n == "consumer" or n.startswith("consumer.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def load_gate_file(path: Path) -> Gate:
    path = path.resolve()
    if path.suffix != ".py" or path.parent.name != "gates":
        raise GateError([f"{path}: a gate is a .py file in a gates/ directory"])
    if not STEM.match(path.stem):
        raise GateError([f"{path.name}: gate file names are lowercase letters, digits and _"])
    register_consumer(path.parent.parent)
    try:
        module = importlib.import_module(f"consumer.gates.{path.stem}")
    except Exception as exc:
        raise GateError([f"{path.name}: cannot be loaded ({type(exc).__name__}: {exc})"]) from None
    gate = getattr(module, "gate", None)
    if not isinstance(gate, Gate):
        raise GateError([f"{path.name}: defines no `gate = Gate(...)`"])
    if gate.name != path.stem:
        raise GateError(
            [f"{path.name}: the gate is named {gate.name!r}; name it {path.stem!r} after its file"]
        )
    return gate


def load_closure(path: Path) -> list[Gate]:
    """The gate and every gate it requires, transitively, required gates first."""
    gates_dir = path.resolve().parent
    first = load_gate_file(path)
    found = {first.name: first}
    pending = list(first.requires)
    problems = []
    while pending:
        name = pending.pop()
        if name in found:
            continue
        candidate = gates_dir / f"{name}.py"
        if not candidate.is_file():
            problems.append(f"gate {name!r} is required but {candidate.name} does not exist")
            continue
        found[name] = load_gate_file(candidate)
        pending.extend(found[name].requires)
    if problems:
        raise GateError(problems)
    return order_gates(list(found.values()))


def load_directory(gates_dir: Path) -> list[Gate]:
    files = sorted(p for p in gates_dir.glob("*.py") if not p.name.startswith("_"))
    if not files:
        raise GateError([f"{gates_dir} holds no gate files"])
    gates, problems = [], []
    for path in files:
        try:
            gates.append(load_gate_file(path))
        except GateError as exc:
            problems.extend(exc.problems)
    names = {g.name for g in gates}
    for gate in gates:
        problems.extend(
            f"gate {gate.name} requires unknown gate {r!r}" for r in gate.requires if r not in names
        )
    if problems:
        raise GateError(problems)
    return order_gates(gates)


def order_gates(gates: list[Gate]) -> list[Gate]:
    by_name = {g.name: g for g in gates}
    state: dict[str, str] = {}
    ordered: list[Gate] = []

    def visit(gate: Gate, trail: list[str]) -> None:
        if state.get(gate.name) == "done":
            return
        if state.get(gate.name) == "active":
            raise GateError([f"gates require each other in a cycle: {' -> '.join([*trail, gate.name])}"])
        state[gate.name] = "active"
        for required in sorted(gate.requires):
            if required in by_name:
                visit(by_name[required], [*trail, gate.name])
        state[gate.name] = "done"
        ordered.append(gate)

    for gate in sorted(gates, key=lambda g: g.name):
        visit(gate, [])
    return ordered
```

Add `Gate` to `src/preflight/__init__.py`: add the line `from preflight.gate import Gate` after the `preflight.check` import block, and add `"Gate",` to `__all__` in alphabetical position.

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS for the whole suite, including the earlier tasks.

- [ ] **Step 6: Commit**

```bash
git add src/preflight/gate.py src/preflight/__init__.py tests/conftest.py tests/test_gate.py
git commit -m "feat(core): gates loaded from the consumer package without touching sys.path"
```

---
### Task 9: DNS client

**Files:**
- Create: `src/preflight/dnsclient.py`, `tests/test_dnsclient.py`

**Interfaces:**
- Produces:
  - `norm(name) -> str` (lowercase, no trailing dot);
  - `DnsUnavailable(Exception)`;
  - the `Transport` protocol, with `query(message, server, timeout) -> Message` and `resolve(name, rdtype) -> list[str]`;
  - `SystemTransport`;
  - `Referral(kind: "referral"|"none", servers: frozenset[str])`;
  - `DnsClient(transport=None, timeout=3.0)`, with `.parent_addresses(parent)`, `.referral(name, parent)`, `.cname(name)` and `.caa(name)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dnsclient.py
import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest

from preflight.dnsclient import DnsClient, DnsUnavailable, Referral, norm

PARENT = {
    ("tellabs.dev", "NS"): ["ns-a.example.", "ns-b.example."],
    ("ns-a.example", "A"): ["192.0.2.1"],
    ("ns-b.example", "A"): ["192.0.2.2"],
}


class FakeTransport:
    def __init__(self, answers=None, responses=None):
        self.answers = {**PARENT, **(answers or {})}
        self.responses = responses or {}
        self.queries = []

    def resolve(self, name, rdtype):
        value = self.answers.get((name, rdtype), [])
        if isinstance(value, Exception):
            raise value
        return value

    def query(self, message, server, timeout):
        self.queries.append((server, message))
        value = self.responses[server]
        if isinstance(value, Exception):
            raise value
        return value(message)


def referral(*servers):
    def answer(query):
        response = dns.message.make_response(query)
        response.authority.append(
            dns.rrset.from_text("app.tellabs.dev.", 172800, "IN", "NS", *servers)
        )
        return response

    return answer


def rcode(code):
    def answer(query):
        response = dns.message.make_response(query)
        response.set_rcode(code)
        return response

    return answer


def test_norm():
    assert norm(" NS-1.Example.COM. ") == "ns-1.example.com"


def test_reads_the_referral_from_the_authority_section_without_recursion():
    transport = FakeTransport(responses={"192.0.2.1": referral("NS-1.example.", "ns-2.example.")})
    result = DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")
    assert result == Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))
    server, message = transport.queries[0]
    assert server == "192.0.2.1"
    assert not message.flags & dns.flags.RD


def test_nxdomain_and_an_empty_authority_mean_no_delegation():
    for answer in (rcode(dns.rcode.NXDOMAIN), rcode(dns.rcode.NOERROR)):
        transport = FakeTransport(responses={"192.0.2.1": answer})
        assert DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev").kind == "none"


def test_a_server_that_times_out_is_skipped():
    transport = FakeTransport(
        responses={"192.0.2.1": dns.exception.Timeout(), "192.0.2.2": referral("ns-1.example.")}
    )
    assert DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev").kind == "referral"


def test_no_usable_answer_is_unavailable_never_none():
    transport = FakeTransport(
        responses={"192.0.2.1": rcode(dns.rcode.SERVFAIL), "192.0.2.2": rcode(dns.rcode.REFUSED)}
    )
    with pytest.raises(DnsUnavailable):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_a_parent_without_name_servers_is_unavailable():
    transport = FakeTransport(answers={("tellabs.dev", "NS"): []})
    with pytest.raises(DnsUnavailable, match="no NS records"):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_cname_and_caa():
    transport = FakeTransport(
        answers={
            ("vault.tellabs.dev", "CNAME"): ["Vault.App.Tellabs.Dev."],
            ("tellabs.dev", "CAA"): ['0 issue "amazon.com"'],
        }
    )
    client = DnsClient(transport)
    assert client.cname("vault.tellabs.dev") == ["vault.app.tellabs.dev"]
    assert client.caa("tellabs.dev") == ['0 issue "amazon.com"']
    assert client.cname("missing.tellabs.dev") == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_dnsclient.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.dnsclient`).

- [ ] **Step 3: Implement**

```python
# src/preflight/dnsclient.py
"""DNS observations: the parent-side delegation of a name, and recursive CNAME and CAA lookups.

A delegation is read from the parent's own servers with a non-recursive query, because a
recursive resolver answers with the child's NS as soon as any delegated server works."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver


class DnsUnavailable(Exception):
    """No server gave a usable answer; never a basis for an ok."""


def norm(name: str) -> str:
    return name.strip().lower().rstrip(".")


class Transport(Protocol):
    def query(self, message: dns.message.Message, server: str, timeout: float) -> dns.message.Message: ...

    def resolve(self, name: str, rdtype: str) -> list[str]: ...


class SystemTransport:
    def query(self, message: dns.message.Message, server: str, timeout: float) -> dns.message.Message:
        response = dns.query.udp(message, server, timeout=timeout)
        if response.flags & dns.flags.TC:
            response = dns.query.tcp(message, server, timeout=timeout)
        return response

    def resolve(self, name: str, rdtype: str) -> list[str]:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        try:
            answer = resolver.resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as exc:
            raise DnsUnavailable(f"{name} {rdtype}: {type(exc).__name__}") from None
        return [record.to_text() for record in answer]


@dataclass(frozen=True)
class Referral:
    kind: Literal["referral", "none"]
    servers: frozenset[str]


class DnsClient:
    def __init__(self, transport: Transport | None = None, timeout: float = 3.0):
        self.transport = transport or SystemTransport()
        self.timeout = timeout

    def parent_addresses(self, parent: str) -> list[str]:
        servers = sorted(norm(s) for s in self.transport.resolve(parent, "NS"))
        if not servers:
            raise DnsUnavailable(f"{parent} has no NS records")
        addresses = [a for server in servers for a in self.transport.resolve(server, "A")]
        if not addresses:
            raise DnsUnavailable(f"the name servers of {parent} have no addresses")
        return addresses

    def referral(self, name: str, parent: str) -> Referral:
        query = dns.message.make_query(name, dns.rdatatype.NS)
        query.flags &= ~dns.flags.RD
        target = dns.name.from_text(name)
        for address in self.parent_addresses(parent):
            try:
                response = self.transport.query(query, address, self.timeout)
            except (dns.exception.DNSException, OSError):
                continue
            code = response.rcode()
            if code == dns.rcode.NXDOMAIN:
                return Referral("none", frozenset())
            if code != dns.rcode.NOERROR:
                continue
            for rrset in response.authority:
                if rrset.rdtype == dns.rdatatype.NS and rrset.name == target:
                    return Referral("referral", frozenset(norm(r.target.to_text()) for r in rrset))
            return Referral("none", frozenset())
        raise DnsUnavailable(f"no server for {parent} answered about {name}")

    def cname(self, name: str) -> list[str]:
        return [norm(target) for target in self.transport.resolve(name, "CNAME")]

    def caa(self, name: str) -> list[str]:
        return self.transport.resolve(name, "CAA")
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_dnsclient.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/dnsclient.py tests/test_dnsclient.py
git commit -m "feat(core): parent-side delegation, cname and caa lookups"
```

---

### Task 10: The check context and test fakes

**Files:**
- Create: `src/preflight/context.py`, `tests/fakes.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: `DnsClient` (Task 9) and `Identity` (Task 6).
- Produces:
  - `Context(root, environment=None, identity=None, identities={}, environ=<os.environ copy>, _host=None, _ansible_host=None, _dns=None)`, with:
    - properties `.host` (testinfra `local://`), `.ansible_host` (testinfra `ansible://localhost`) and `.dns`;
    - `.path(relative) -> Path`;
    - `.aws_module(module, args=None, *, identity=None, ambient=False) -> dict`;
    - `.ssm_lookup(name, *, identity=None) -> str | None`.
  - `make_ansible_host()`.
- Test fakes (used by every catalog task):
  - `FakeResult(rc=0, stdout="", stderr="")`;
  - `FakeFile(exists, content_string="")`;
  - `FakeHost(rules=[(substring, FakeResult)], files={abs_path: text})`, with `.commands`;
  - `FakeAnsibleHost(modules={name: dict|Exception|callable(args)})`, with `.calls`;
  - `FakeDns(referrals={}, cnames={}, caa={})`;
  - `IDENTITY` and `ROLE_ARN`;
  - `make_ctx(root, *, host=None, ansible=None, dns=None, identity=IDENTITY, environ=None, environment="dev")`.

- [ ] **Step 1: Write the fakes**

```python
# tests/fakes.py
"""Fakes for testinfra hosts, Ansible modules and DNS, used by the catalog tests."""

import json
import shlex
from dataclasses import dataclass

from preflight.context import Context
from preflight.identity import Identity

IDENTITY = Identity(
    profile="sandbox",
    region="us-east-1",
    account_id="111111111111",
    permission_set="AWSAdministratorAccess",
)
ROLE_ARN = (
    "arn:aws:sts::111111111111:assumed-role/"
    "AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d/operator"
)


@dataclass
class FakeResult:
    rc: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self):
        return self.rc == 0


@dataclass
class FakeFile:
    exists: bool
    content_string: str = ""


class FakeHost:
    """Answers `run` from (substring, result) rules, first match wins; records every command."""

    def __init__(self, rules=(), files=None):
        self.rules = list(rules)
        self.files = dict(files or {})
        self.commands = []

    def run(self, command, *args):
        rendered = command % tuple(shlex.quote(str(a)) for a in args) if args else command
        self.commands.append(rendered)
        for pattern, result in self.rules:
            if pattern in rendered:
                return result
        raise AssertionError(f"unexpected command: {rendered}")

    def file(self, path):
        path = str(path)
        return FakeFile(True, self.files[path]) if path in self.files else FakeFile(False)


class FakeAnsibleHost:
    """Answers `ansible(module, args)` from a table of results, exceptions or callables."""

    def __init__(self, modules):
        self.modules = dict(modules)
        self.calls = []

    def ansible(self, module, module_args=None, check=True, **kwargs):
        args = json.loads(module_args) if module_args else {}
        self.calls.append((module, args))
        answer = self.modules[module]
        if callable(answer) and not isinstance(answer, BaseException):
            answer = answer(args)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeDns:
    def __init__(self, referrals=None, cnames=None, caa=None):
        self.referrals = referrals or {}
        self.cnames = cnames or {}
        self.caa_records = caa or {}

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def referral(self, name, parent):
        return self._answer(self.referrals[name])

    def cname(self, name):
        return self._answer(self.cnames.get(name, []))

    def caa(self, name):
        return self._answer(self.caa_records.get(name, []))


def make_ctx(
    root, *, host=None, ansible=None, dns=None, identity=IDENTITY, environ=None, environment="dev"
):
    return Context(
        root=root,
        environment=environment,
        identity=identity,
        identities={"admin": identity} if identity else {},
        environ=environ if environ is not None else {},
        _host=host,
        _ansible_host=ansible,
        _dns=dns,
    )
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_context.py
import os
import sys

import pytest
from fakes import FakeAnsibleHost, make_ctx

from preflight import context as context_module


def test_aws_modules_get_the_profile_and_region(tmp_path):
    ansible = FakeAnsibleHost({"amazon.aws.aws_caller_info": {"account": "1"}})
    ctx = make_ctx(tmp_path, ansible=ansible)
    assert ctx.aws_module("amazon.aws.aws_caller_info") == {"account": "1"}
    ctx.aws_module("amazon.aws.aws_caller_info", {"x": 1}, ambient=True)
    assert ansible.calls == [
        ("amazon.aws.aws_caller_info", {"region": "us-east-1", "profile": "sandbox"}),
        ("amazon.aws.aws_caller_info", {"x": 1, "region": "us-east-1"}),
    ]


@pytest.mark.parametrize(("msg", "expected"), [("value", "value"), ("", None), ("None", None)])
def test_ssm_lookup_reads_without_decryption(tmp_path, msg, expected):
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"msg": msg}})
    assert make_ctx(tmp_path, ansible=ansible).ssm_lookup("/platform/state/bucket") == expected
    (module, args), = ansible.calls
    assert module == "ansible.builtin.debug"
    assert args["msg"] == (
        "{{ lookup('amazon.aws.ssm_parameter', '/platform/state/bucket', decrypt=False, "
        "on_missing='skip', profile='sandbox', region='us-east-1') }}"
    )


def test_ssm_lookup_refuses_unsafe_names(tmp_path):
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({}))
    with pytest.raises(ValueError):
        ctx.ssm_lookup("/x', profile='other")


def test_a_check_without_an_identity_cannot_call_aws(tmp_path):
    with pytest.raises(RuntimeError, match="no identity"):
        make_ctx(tmp_path, identity=None, ansible=FakeAnsibleHost({})).aws_module("m")


def test_the_ansible_host_uses_a_local_inventory_with_this_python(monkeypatch):
    captured = {}

    def fake_get_host(spec, **kwargs):
        captured["spec"] = spec
        captured["inventory"] = open(kwargs["ansible_inventory"]).read()
        return "host"

    monkeypatch.setattr(context_module.testinfra, "get_host", fake_get_host)
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    assert context_module.make_ansible_host() == "host"
    assert captured["spec"] == "ansible://localhost"
    assert captured["inventory"] == (
        f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n"
    )
    assert os.path.isfile(os.environ["ANSIBLE_CONFIG"])
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_context.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.context`).

- [ ] **Step 4: Implement**

```python
# src/preflight/context.py
"""What a check observes through: testinfra hosts, Ansible modules, DNS, and its identity."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import testinfra

from preflight.dnsclient import DnsClient
from preflight.identity import Identity

SAFE = re.compile(r"^[A-Za-z0-9_.\-/+=,@]+$")
# How the SSM lookup renders a missing parameter with on_missing='skip' (see Spike results).
MISSING = (None, "", "None")


def _literal(value: str) -> str:
    if not SAFE.match(value):
        raise ValueError("value is not safe to place in a lookup expression")
    return f"'{value}'"


def make_ansible_host() -> Any:
    directory = tempfile.mkdtemp(prefix="preflight-ansible-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    inventory = Path(directory) / "inventory.ini"
    inventory.write_text(
        f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n"
    )
    config = Path(directory) / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\nlocalhost_warning = False\n")
    # A consumer's own ansible.cfg in the repo root must not change how probes run.
    os.environ["ANSIBLE_CONFIG"] = str(config)
    return testinfra.get_host("ansible://localhost", ansible_inventory=str(inventory))


@dataclass
class Context:
    root: Path
    environment: str | None = None
    identity: Identity | None = None
    identities: Mapping[str, Identity] = field(default_factory=dict)
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    _host: Any = None
    _ansible_host: Any = None
    _dns: DnsClient | None = None

    @property
    def host(self) -> Any:
        if self._host is None:
            self._host = testinfra.get_host("local://")
        return self._host

    @property
    def ansible_host(self) -> Any:
        if self._ansible_host is None:
            self._ansible_host = make_ansible_host()
        return self._ansible_host

    @property
    def dns(self) -> DnsClient:
        if self._dns is None:
            self._dns = DnsClient()
        return self._dns

    def path(self, relative: str) -> Path:
        return self.root / relative

    def _identity(self, identity: Identity | None) -> Identity:
        chosen = identity or self.identity
        if chosen is None:
            raise RuntimeError("this check has no identity")
        return chosen

    def aws_module(
        self,
        module: str,
        args: Mapping[str, Any] | None = None,
        *,
        identity: Identity | None = None,
        ambient: bool = False,
    ) -> dict[str, Any]:
        chosen = self._identity(identity)
        payload = {**(args or {}), "region": chosen.region}
        if not ambient:
            payload["profile"] = chosen.profile
        return self.ansible_host.ansible(module, json.dumps(payload), check=True)

    def ssm_lookup(self, name: str, *, identity: Identity | None = None) -> str | None:
        chosen = self._identity(identity)
        expression = (
            "{{ lookup('amazon.aws.ssm_parameter', %s, decrypt=False, on_missing='skip', "
            "profile=%s, region=%s) }}"
        ) % (_literal(name), _literal(chosen.profile), _literal(chosen.region))
        result = self.ansible_host.ansible(
            "ansible.builtin.debug", json.dumps({"msg": expression}), check=True
        )
        value = result.get("msg")
        return None if value in MISSING else str(value)
```

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_context.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/preflight/context.py tests/fakes.py tests/test_context.py
git commit -m "feat(core): check context over testinfra and Ansible, with test fakes"
```

---

### Task 11: Catalog — AWS sessions

**Files:**
- Create: `src/preflight/catalog/aws.py`, `tests/test_catalog_aws.py`

**Interfaces:**
- Consumes:
  - `check`, `IdentitySection` and `session_for` (Task 4);
  - `ok`, `fail`, `error` and `outcome` (Task 3);
  - `Context.aws_module`, `Context.host` and `Context.environ` (Task 10).
- Produces three checks and a helper. The checks, all identity-bound:
  - `aws.session` (`preflight.catalog.aws.session`);
  - `aws.assumed` (`ambient=True`);
  - `aws.region`.

  The helper `session_for` is re-exported.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_aws.py
import json

from fakes import ROLE_ARN, FakeAnsibleHost, FakeHost, FakeResult, make_ctx
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import aws
from preflight.check import IdentitySection
from preflight.outcome import Status

SECTION = IdentitySection(identity="admin")
OTHER_ARN = ROLE_ARN.replace("111111111111", "222222222222")


def caller(account="111111111111", arn=ROLE_ARN):
    return FakeAnsibleHost({"amazon.aws.aws_caller_info": {"account": account, "arn": arn}})


def test_session_ok_observes_with_the_named_profile(tmp_path):
    ansible = caller()
    result = aws.session.observe(make_ctx(tmp_path, ansible=ansible), SECTION)
    assert result.status is Status.OK
    assert ansible.calls == [
        ("amazon.aws.aws_caller_info", {"region": "us-east-1", "profile": "sandbox"})
    ]


def test_session_mismatch_names_the_expected_role(tmp_path):
    ctx = make_ctx(tmp_path, ansible=caller("222222222222", OTHER_ARN))
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.FAIL
    assert "AWSReservedSSO_AWSAdministratorAccess_*" in item.next_step.do
    assert item.next_step.paste == "aws sso login --profile sandbox"


def test_session_error_keeps_the_module_message_out(tmp_path):
    failure = AnsibleException({"failed": True, "msg": "token expired SECRET"})
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failure}))
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "AnsibleException"
    assert item.next_step.paste == "aws sso login --profile sandbox"
    assert "SECRET" not in json.dumps(item.to_dict())


def test_assumed_observes_the_callers_environment_and_names_what_to_change(tmp_path):
    ansible = caller("222222222222", OTHER_ARN)
    environ = {"AWS_ACCESS_KEY_ID": "k", "AWS_SESSION_TOKEN": "t", "AWS_PROFILE": "production"}
    item = aws.assumed.observe(make_ctx(tmp_path, ansible=ansible, environ=environ), SECTION).items[0]
    assert aws.assumed.ambient is True
    assert item.status is Status.FAIL
    assert item.next_step.paste == "unset AWS_ACCESS_KEY_ID AWS_SESSION_TOKEN\nexport AWS_PROFILE=sandbox"
    assert ansible.calls == [("amazon.aws.aws_caller_info", {"region": "us-east-1"})]


def test_assumed_ok(tmp_path):
    ctx = make_ctx(tmp_path, ansible=caller(), environ={"AWS_PROFILE": "sandbox"})
    assert aws.assumed.observe(ctx, SECTION).status is Status.OK


def test_assumed_without_credentials_is_an_error_with_the_fix(tmp_path):
    ctx = make_ctx(
        tmp_path,
        ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": AnsibleException({"failed": True})}),
    )
    item = aws.assumed.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.next_step.paste == "export AWS_PROFILE=sandbox\naws sso login --profile sandbox"


def test_region(tmp_path):
    good = FakeHost([("aws configure get region --profile sandbox", FakeResult(0, "us-east-1\n"))])
    unset = FakeHost([("aws configure get region", FakeResult(1, ""))])
    missing = FakeHost([("aws configure get region", FakeResult(127, ""))])
    assert aws.region.observe(make_ctx(tmp_path, host=good), SECTION).status is Status.OK
    item = aws.region.observe(make_ctx(tmp_path, host=unset), SECTION).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "aws configure set region us-east-1 --profile sandbox"
    item = aws.region.observe(make_ctx(tmp_path, host=missing), SECTION).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "MissingTool")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_aws.py -v`
Expected: FAIL (`ImportError: cannot import name 'aws'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/aws.py
"""AWS sessions: can preflight observe as an identity (`aws.session`), will the caller's next
command act as it (`aws.assumed`), and is the profile's region the expected one (`aws.region`)."""

from __future__ import annotations

from preflight.check import IdentitySection, check, session_for
from preflight.outcome import Outcome, error, fail, ok, outcome

__all__ = ["assumed", "region", "session", "session_for"]

CREDENTIAL_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
)


def _caller(ctx, *, ambient: bool) -> tuple[str, str]:
    info = ctx.aws_module("amazon.aws.aws_caller_info", {}, ambient=ambient)
    return str(info.get("account", "")), str(info.get("arn", ""))


@check("aws.session", binds="identity")
def session(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    login = f"aws sso login --profile {identity.profile}"
    try:
        account, arn = _caller(ctx, ambient=False)
    except Exception as exc:
        return outcome(
            error(
                do=f"Sign in to profile {identity.profile}.",
                paste=login,
                error_type=type(exc).__name__,
            )
        )
    observed = {"account": account, "arn": arn}
    if identity.matches(account, arn):
        return outcome(ok(observed=observed))
    return outcome(
        fail(
            do=(
                f"Profile {identity.profile} signs in as {arn or 'nothing'} in account "
                f"{account or 'unknown'}, but this contract expects {identity.describe()}. "
                "Point the profile at that account and permission set in ~/.aws/config, "
                "then sign in again."
            ),
            paste=login,
            observed=observed,
        )
    )


def _environment_fix(environ, identity) -> str:
    lines = []
    stray = [name for name in CREDENTIAL_VARIABLES if name in environ]
    if stray:
        lines.append("unset " + " ".join(stray))
    if environ.get("AWS_PROFILE") != identity.profile:
        lines.append(f"export AWS_PROFILE={identity.profile}")
    return "\n".join(lines)


@check("aws.assumed", binds="identity", ambient=True)
def assumed(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    fix = _environment_fix(ctx.environ, identity)
    login = f"aws sso login --profile {identity.profile}"
    try:
        account, arn = _caller(ctx, ambient=True)
    except Exception as exc:
        return outcome(
            error(
                do=f"Your shell has no working AWS credentials; select profile {identity.profile} and sign in.",
                paste=f"{fix}\n{login}" if fix else login,
                error_type=type(exc).__name__,
            )
        )
    observed = {"account": account, "arn": arn}
    if identity.matches(account, arn):
        return outcome(ok(observed=observed))
    return outcome(
        fail(
            do=(
                f"Your shell acts as {arn} in account {account}; the next command needs "
                f"{identity.describe()}."
            ),
            paste=fix or f"export AWS_PROFILE={identity.profile}",
            observed=observed,
        )
    )


@check("aws.region", binds="identity")
def region(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    result = ctx.host.run("aws configure get region --profile %s", identity.profile)
    if result.rc == 127:
        return outcome(error(do="Install the AWS CLI.", error_type="MissingTool"))
    configured = result.stdout.strip()
    if result.rc == 0 and configured == identity.region:
        return outcome(ok(observed=configured))
    return outcome(
        fail(
            do=(
                f"Profile {identity.profile} has region {configured or 'unset'}; "
                f"set it to {identity.region}."
            ),
            paste=f"aws configure set region {identity.region} --profile {identity.profile}",
            observed=configured or None,
        )
    )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_aws.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. If ruff reports E501 on the long `do=` string in `assumed`, split it into two adjacent string literals.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/aws.py tests/test_catalog_aws.py
git commit -m "feat(catalog): aws.session, aws.assumed and aws.region"
```

---

### Task 12: Catalog — SSM parameters

**Files:**
- Create: `src/preflight/catalog/ssm.py`, `tests/test_catalog_ssm.py`

**Interfaces:**
- Consumes: `Context.ssm_lookup` (Task 10) and `session_for` (Task 4).
- Produces:
  - `PresentSection(identity, name, how=None)` and `ParametersSection(identity, names, how=None)`;
  - the checks `ssm.present` and `ssm.parameters`.

  The graph (Task 13) creates `ssm.present[<name>]` nodes with data `{"identity", "name", "how"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_ssm.py
import re

from fakes import FakeAnsibleHost, make_ctx
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import ssm
from preflight.outcome import Status

NAME = re.compile(r"ssm_parameter', '([^']+)'")


def store(values):
    def answer(args):
        value = values[NAME.search(args["msg"])[1]]
        if isinstance(value, BaseException):
            raise value
        return {"msg": value}

    return FakeAnsibleHost({"ansible.builtin.debug": answer})


def test_present(tmp_path):
    section = ssm.PresentSection(identity="admin", name="/platform/state/bucket", how="rerun bootstrap")
    ok_ctx = make_ctx(tmp_path, ansible=store({"/platform/state/bucket": "tellabs-tfstate-dev"}))
    assert ssm.present.observe(ok_ctx, section).status is Status.OK
    missing = make_ctx(tmp_path, ansible=store({"/platform/state/bucket": ""}))
    item = ssm.present.observe(missing, section).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.do == "SSM parameter /platform/state/bucket is missing or empty."
    assert item.next_step.paste == "rerun bootstrap"
    assert item.next_step.generic is True
    assert item.observed is None


def test_parameters_reports_each_name(tmp_path):
    section = ssm.ParametersSection(identity="admin", names=["/a", "/b", "/c"])
    ctx = make_ctx(
        tmp_path,
        ansible=store({"/a": "1", "/b": "None", "/c": AnsibleException({"failed": True})}),
    )
    items = {i.key: i for i in ssm.parameters.observe(ctx, section).items}
    assert items["/a"].status is Status.OK
    assert items["/b"].status is Status.FAIL
    assert (items["/c"].status, items["/c"].error_type) == (Status.ERROR, "AnsibleException")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_ssm.py -v`
Expected: FAIL (`ImportError: cannot import name 'ssm'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/ssm.py
"""SSM parameters another stage publishes: present and non-empty. Values are read without
decryption and never reported."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from preflight.check import IdentityRef, Section, check, session_for
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

SsmName = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_.\-/]+$")]


class PresentSection(Section):
    identity: IdentityRef
    name: SsmName
    how: str | None = None


class ParametersSection(Section):
    identity: IdentityRef
    names: list[SsmName] = Field(min_length=1)
    how: str | None = None


def _observe(ctx, name: str, how: str | None, key: str | None) -> Item:
    try:
        value = ctx.ssm_lookup(name)
    except Exception as exc:
        return error(
            key,
            do=f"Could not read {name}; check that profile {ctx.identity.profile} may call ssm:GetParameter.",
            error_type=type(exc).__name__,
        )
    if value:
        return ok(key)
    return fail(key, do=f"SSM parameter {name} is missing or empty.", paste=how, generic=True)


@check("ssm.present", section=PresentSection, requires=[session_for("identity")])
def present(ctx, s: PresentSection) -> Outcome:
    return outcome(_observe(ctx, s.name, s.how, None))


@check("ssm.parameters", section=ParametersSection, requires=[session_for("identity")])
def parameters(ctx, s: ParametersSection) -> Outcome:
    return outcome(*(_observe(ctx, name, s.how, name) for name in s.names))
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_ssm.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. If ruff reports E501 on the `do=` line, split the f-string into two adjacent literals.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/ssm.py tests/test_catalog_ssm.py
git commit -m "feat(catalog): ssm.present and ssm.parameters"
```

---

### Task 13: The instance graph

**Files:**
- Create: `src/preflight/graph.py`, `tests/sample_checks.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes:
  - `REGISTRY`, `CheckInstance` and `field_problems` (Task 4);
  - `Contract` and `Placeholder` (Task 7);
  - `Gate` (Task 8);
  - `LazySsm` (Task 5);
  - the catalog checks `aws.session` (Task 11) and `ssm.present` (Task 12).
- Produces:
  - `Node(id, check_id, key, gates, contract, data, identity, requires=[], timeout=60.0, static=None, order=0, section_bound=True)`, with `.owner`, `.remedy()` and `.template_values()`.
  - `Plan(nodes, gates, contracts)`, with `.ordered()`, `.nodes_of(gate)` and `.inputs`.
  - `GraphError(problems)`.
  - `build_plan(gates, contracts: Mapping[scope, Contract], *, link_gates: bool, strict: bool) -> Plan`.
  - `iter_lazy(value)`.

- [ ] **Step 1: Write the in-process sample checks**

```python
# tests/sample_checks.py
"""Checks for the graph and runner tests; they never observe anything."""

from preflight import IdentityRef, Section, check, ok, outcome, session_for


class Things(Section):
    items: list[str] = []


class AwsThing(Section):
    identity: IdentityRef
    name_servers: dict[str, list[str]] = {}


@check("graph.thing", section=Things)
def thing(ctx, s):
    return outcome(ok())


@check("graph.aws_thing", section=AwsThing, requires=[session_for("identity")])
def aws_thing(ctx, s):
    return outcome(ok())
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_graph.py
import pytest
import sample_checks as sc
from conftest import write

from preflight.contract import load_contract
from preflight.gate import Gate
from preflight.graph import GraphError, build_plan
from preflight.outcome import Status

CONTRACT = """
schema_version = 1
environment = "dev"

[identities.admin]
profile = "sandbox"
region = "us-east-1"
account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"] }
permission_set = "AWSAdministratorAccess"

[things]
items = ["a"]

[dns]
identity = "admin"
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json", how = "rerun bootstrap" }

[dns.remedy]
do = "Rerun bootstrap for {environment}."
"""


def contract(repo, text=CONTRACT, account="111111111111"):
    write(repo, "envs/dev.tfvars", f'account_id = "{account}"\n')
    return load_contract(write(repo, "preflight/contracts/dev.toml", text))


def plan(gates, c, *, link=True, strict=False):
    return build_plan(gates, {c.scope: c}, link_gates=link, strict=strict)


def test_sessions_and_ssm_parameters_become_prerequisites(repo):
    p = plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo))
    assert [n.id for n in p.ordered()] == [
        "aws.session[admin]",
        "ssm.present[/platform/dns/name_servers]",
        "graph.aws_thing[dns]",
    ]
    assert p.nodes["graph.aws_thing[dns]"].requires == [
        "aws.session[admin]",
        "ssm.present[/platform/dns/name_servers]",
    ]
    assert p.nodes["ssm.present[/platform/dns/name_servers]"].data == {
        "identity": "admin",
        "name": "/platform/dns/name_servers",
        "how": "rerun bootstrap",
    }
    assert p.nodes["aws.session[admin]"].gates == ["g"]


def test_an_identity_placeholder_blocks_its_session(repo):
    p = plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, account="000000000000"))
    placeholder_id = "contract.placeholder[identities.admin.account_id]"
    assert p.nodes["aws.session[admin]"].requires == [placeholder_id]
    placeholder = p.nodes[placeholder_id]
    assert placeholder.static.status is Status.FAIL
    assert placeholder.static.items[0].next_step.do == "Fill `account_id` in `envs/dev.tfvars`."
    assert p.ordered()[0].id == placeholder_id


def test_missing_sections_and_bad_fields_are_reported_together(repo):
    text = CONTRACT.replace('items = ["a"]', "items = [1]")
    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[sc.thing("things"), sc.thing("nope")])], contract(repo, text))
    assert caught.value.problems == [
        "[things] for graph.thing: items.0: Input should be a valid string",
        "g: graph.thing[nope]: no section [nope] in dev.toml",
    ]


def test_remedy_templates_are_checked(repo):
    text = CONTRACT.replace("{environment}", "{nope}")
    with pytest.raises(GraphError, match=r"\[dns.remedy\].do: unknown template field 'nope'"):
        plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, text))


def test_strict_mode_refuses_unused_sections_and_keys(repo):
    text = CONTRACT.replace('items = ["a"]', 'items = ["a"]\nstray = 1')
    gates = [Gate("g", checks=[sc.thing("things")])]
    plan(gates, contract(repo, text))
    with pytest.raises(GraphError) as caught:
        plan(gates, contract(repo, text), strict=True)
    assert caught.value.problems == [
        "[things].stray is used by no check bound to [things]",
        "[dns] in dev.toml is used by no gate",
    ]


def test_linked_gates_make_required_instances_prerequisites(repo):
    c = contract(repo)
    a = Gate("a", checks=[sc.thing("things")])
    b = Gate("b", checks=[sc.aws_thing("dns")], requires=["a"])
    linked = plan([a, b], c)
    assert "graph.thing[things]" in linked.nodes["graph.aws_thing[dns]"].requires
    assert "graph.thing[things]" in linked.nodes["aws.session[admin]"].requires
    unlinked = plan([a, b], c, link=False)
    assert "graph.thing[things]" not in unlinked.nodes["graph.aws_thing[dns]"].requires


def test_an_instance_in_two_gates_is_one_node(repo):
    gates = [Gate("a", checks=[sc.thing("things")]), Gate("b", checks=[sc.thing("things")])]
    p = plan(gates, contract(repo), link=False)
    assert p.nodes["graph.thing[things]"].gates == ["a", "b"]


def test_repository_nodes_are_prefixed_in_an_environment_plan(repo):
    env = contract(repo)
    repository = load_contract(
        write(
            repo,
            "preflight/contracts/repo.toml",
            'schema_version = 1\nscope = "repository"\n\n[things]\nitems = []\n',
        )
    )
    gates = [
        Gate("r", checks=[sc.thing("things")], scope="repository"),
        Gate("e", checks=[sc.thing("things")], requires=["r"]),
    ]
    p = build_plan(
        gates, {"environment": env, "repository": repository}, link_gates=True, strict=False
    )
    assert p.nodes["graph.thing[things]"].requires == ["repository/graph.thing[things]"]
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.graph`).

- [ ] **Step 4: Implement**

```python
# src/preflight/graph.py
"""The instance graph: every gate's instances plus their implicit prerequisites (sessions,
SSM parameters, placeholders), in dependency order."""

from __future__ import annotations

import heapq
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
        return {k: v for k, v in remedy.items() if isinstance(v, str)} if isinstance(remedy, dict) else {}

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
        if gate.name not in self.nodes[node_id].gates:
            self.nodes[node_id].gates.append(gate.name)

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
                self.problems.append(f"{where}: no [identities.{instance.key}] in {contract.path.name}")
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
                self.problems.append(f"{where}: no section [{instance.key}] in {contract.path.name}")
                return None
            data = contract.sections[instance.key]
            self.bound.setdefault((gate.scope, instance.key), []).append(check.section)
            skip = contract.placeholder_fields(instance.key) | contract.lazy_fields(instance.key)
            self.problems.extend(
                f"[{instance.key}] for {check.id}: {problem}"
                for problem in field_problems(check.section, data, skip)
            )
            identity = data.get("identity") if isinstance(data.get("identity"), str) else None
            if identity is not None and identity not in contract.identity_data:
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
            alias = node.data.get(requirement.field)
            if isinstance(alias, str) and alias in contract.identity_data:
                dependency = self.add(REGISTRY[requirement.check_id](alias), gate)
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
        for name, text in remedy.items():
            if not isinstance(text, str):
                continue
            try:
                text.format_map(values)
            except (KeyError, IndexError, ValueError) as exc:
                self.problems.append(f"[{section}.remedy].{name}: unknown template field {exc}")

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
```

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_graph.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. Wrap any line ruff reports as too long.

- [ ] **Step 6: Commit**

```bash
git add src/preflight/graph.py tests/sample_checks.py tests/test_graph.py
git commit -m "feat(core): the instance graph with implicit sessions, ssm and placeholders"
```

---

### Task 14: Workers

**Files:**
- Create: `src/preflight/worker.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes:
  - `REGISTRY` (Task 4);
  - `Context` (Task 10);
  - `register_consumer` (Task 8);
  - `Identity` (Task 6);
  - `Outcome`, `error` and `outcome` (Task 3);
  - `LazySsm`, `ResolveError` and `apply_ssm_value` (Task 5).
- Produces:
  - `Job(node_id, check_id, module, consumer_dir, root, environment, data, identity, identities)`, with `.to_json()` and `Job.from_json()`;
  - `encode_data(value)` and `decode_data(value)`;
  - `execute(job, *, context_factory=Context) -> Outcome`;
  - `launch(job, *, env, timeout, python=sys.executable) -> Outcome`;
  - `kill_all()`;
  - `main()`, the entry point for `python -m preflight.worker`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker.py
import json
import os
import time

import pytest
from conftest import write
from fakes import IDENTITY, FakeAnsibleHost, make_ctx

from preflight.outcome import Status
from preflight.resolvers import LazySsm
from preflight.worker import Job, decode_data, encode_data, execute, launch

CHECKS = """
import os
import subprocess
import time
from pathlib import Path

from preflight import Section, check, fail, ok, outcome


class Plain(Section):
    mode: str = "ok"
    servers: dict[str, list[str]] = {}


@check("worker.sample", section=Plain)
def sample(ctx, s):
    if s.mode == "fail":
        return outcome(fail(do="Fix it."))
    if s.mode == "raise":
        raise RuntimeError("password=hunter2")
    if s.mode == "wrong":
        return "not an outcome"
    if s.mode == "sleep":
        child = subprocess.Popen(["sleep", "30"])
        Path(ctx.root, "child.pid").write_text(str(child.pid))
        time.sleep(30)
    if s.mode == "print":
        os.write(1, b"junk")
    if s.mode == "exit":
        os._exit(3)
    return outcome(ok(observed=s.servers))
"""


def job(repo, data):
    write(repo, "preflight/checks/workerchecks.py", CHECKS)
    return Job(
        node_id="worker.sample[x]",
        check_id="worker.sample",
        module="consumer.checks.workerchecks",
        consumer_dir=str(repo / "preflight"),
        root=str(repo),
        environment="dev",
        data=encode_data(data),
        identity=None,
        identities={"admin": IDENTITY.model_dump()},
    )


def ssm_returning(text):
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"msg": text}})
    return lambda **kw: make_ctx(kw["root"], ansible=ansible)


def test_lazy_values_survive_encoding():
    data = {"a": [LazySsm("/x", "admin")], "b": 1}
    assert decode_data(json.loads(json.dumps(encode_data(data)))) == data


def test_execute_returns_the_checks_outcome(repo):
    assert execute(job(repo, {"mode": "fail"})).status is Status.FAIL


def test_an_exception_becomes_an_error_carrying_only_its_type(repo):
    result = execute(job(repo, {"mode": "raise"}))
    assert result.items[0].error_type == "RuntimeError"
    assert "hunter2" not in json.dumps(result.to_dict())


def test_a_non_outcome_is_an_error(repo):
    assert execute(job(repo, {"mode": "wrong"})).items[0].error_type == "TypeError"


def test_lazy_ssm_values_are_resolved_before_validation(repo):
    data = {"servers": LazySsm("/platform/dns/name_servers", "admin", "json")}
    result = execute(job(repo, data), context_factory=ssm_returning('{"app": ["ns-1"]}'))
    assert result.items[0].observed == {"app": ["ns-1"]}


def test_a_lazy_value_of_the_wrong_shape_names_the_field(repo):
    data = {"servers": LazySsm("/platform/dns/name_servers", "admin", "json")}
    item = execute(job(repo, data), context_factory=ssm_returning('["a"]')).items[0]
    assert item.error_type == "ValidationError"
    assert "servers" in item.next_step.do


def test_launch_runs_the_check_in_a_worker_process(repo):
    assert launch(job(repo, {"mode": "fail"}), env=dict(os.environ), timeout=60).status is Status.FAIL


def test_a_timeout_kills_the_whole_process_group(repo):
    result = launch(job(repo, {"mode": "sleep"}), env=dict(os.environ), timeout=5)
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


def test_stray_output_and_crashes_are_errors_never_ok(repo):
    env = dict(os.environ)
    assert launch(job(repo, {"mode": "print"}), env=env, timeout=60).items[0].error_type == "WorkerOutput"
    assert launch(job(repo, {"mode": "exit"}), env=env, timeout=60).items[0].error_type == "WorkerCrashed"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_worker.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.worker`).

- [ ] **Step 3: Implement**

```python
# src/preflight/worker.py
"""One check instance in its own process.

`python -m preflight.worker` reads a job as JSON on stdin and writes the outcome as JSON on
stdout. Nothing else leaves the process: the launcher discards stderr, and kills the whole
process group when the instance runs out of time."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from preflight.check import REGISTRY
from preflight.context import Context
from preflight.gate import register_consumer
from preflight.identity import Identity
from preflight.outcome import Outcome, error, outcome
from preflight.resolvers import LazySsm, ResolveError, apply_ssm_value


@dataclass(frozen=True)
class Job:
    node_id: str
    check_id: str
    module: str
    consumer_dir: str | None
    root: str
    environment: str | None
    data: dict[str, Any]
    identity: dict[str, Any] | None
    identities: dict[str, dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Job:
        return cls(**json.loads(text))


def encode_data(value: Any) -> Any:
    if isinstance(value, LazySsm):
        return value.to_dict()
    if isinstance(value, dict):
        return {key: encode_data(element) for key, element in value.items()}
    if isinstance(value, list):
        return [encode_data(element) for element in value]
    return value


def decode_data(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__lazy_ssm__"}:
            return LazySsm.from_dict(value)
        return {key: decode_data(element) for key, element in value.items()}
    if isinstance(value, list):
        return [decode_data(element) for element in value]
    return value


class _Unresolved(Exception):
    pass


def _resolve_lazy(value: Any, ctx: Context) -> Any:
    if isinstance(value, LazySsm):
        identity = ctx.identities.get(value.identity)
        if identity is None:
            raise _Unresolved(
                f"SSM parameter {value.name} needs identity {value.identity}, which is not filled in."
            )
        raw = ctx.ssm_lookup(value.name, identity=identity)
        if raw is None:
            raise _Unresolved(f"SSM parameter {value.name} is missing.")
        try:
            return apply_ssm_value(raw, value)
        except ResolveError as exc:
            raise _Unresolved(f"SSM parameter {value.name}: {exc}.") from None
    if isinstance(value, dict):
        return {key: _resolve_lazy(element, ctx) for key, element in value.items()}
    if isinstance(value, list):
        return [_resolve_lazy(element, ctx) for element in value]
    return value


def execute(job: Job, *, context_factory: Callable[..., Context] = Context) -> Outcome:
    """Runs inside the worker; every failure becomes an error outcome carrying only a type."""
    try:
        if job.consumer_dir:
            register_consumer(Path(job.consumer_dir))
        importlib.import_module(job.module)
        check = REGISTRY[job.check_id]
        identities = {alias: Identity.model_validate(spec) for alias, spec in job.identities.items()}
        identity = Identity.model_validate(job.identity) if job.identity else None
    except Exception as exc:
        return outcome(
            error(
                do=f"Preflight could not load {job.check_id} ({type(exc).__name__}).",
                error_type=type(exc).__name__,
            )
        )
    ctx = context_factory(
        root=Path(job.root), environment=job.environment, identity=identity, identities=identities
    )
    try:
        data = _resolve_lazy(decode_data(job.data), ctx)
    except _Unresolved as exc:
        return outcome(error(do=str(exc), error_type="Unresolved"))
    except Exception as exc:
        return outcome(
            error(
                do=f"Could not read an SSM value for {job.check_id}; check the session, then rerun.",
                error_type=type(exc).__name__,
            )
        )
    try:
        section = check.section.model_validate(data)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        return outcome(
            error(
                do=(
                    f"After reading SSM, field(s) {', '.join(fields)} have the wrong shape; "
                    "check the parameters they reference."
                ),
                error_type="ValidationError",
            )
        )
    try:
        result = check.observe(ctx, section)
    except Exception as exc:
        return outcome(
            error(
                do=f"Could not observe {job.check_id}; check credentials, network and tools, then rerun.",
                error_type=type(exc).__name__,
            )
        )
    if not isinstance(result, Outcome):
        return outcome(
            error(
                do=f"{job.check_id} returned {type(result).__name__}, not an Outcome; this is a bug in the check.",
                error_type="TypeError",
            )
        )
    return result


_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def kill_all() -> None:
    with _LIVE_LOCK:
        for process in list(_LIVE):
            _kill_group(process)


def launch(
    job: Job, *, env: Mapping[str, str], timeout: float, python: str = sys.executable
) -> Outcome:
    process = subprocess.Popen(
        [python, "-m", "preflight.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=job.root,
        env=dict(env),
        start_new_session=True,
        text=True,
    )
    with _LIVE_LOCK:
        _LIVE.add(process)
    try:
        try:
            stdout, _ = process.communicate(job.to_json(), timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            process.communicate()
            return outcome(
                error(
                    do=(
                        f"Timed out after {timeout:g} s; rerun, or raise this section's timeout "
                        "if the probe is legitimately slow."
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
                do=f"The worker for {job.check_id} exited with status {process.returncode}; rerun, and report it if it persists.",
                error_type="WorkerCrashed",
            )
        )
    try:
        return Outcome.from_dict(json.loads(stdout))
    except (ValueError, KeyError, TypeError):
        return outcome(
            error(
                do=f"The worker for {job.check_id} produced unreadable output; a check may be printing to stdout.",
                error_type="WorkerOutput",
            )
        )


def main() -> int:
    job = Job.from_json(sys.stdin.read())
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # a check that prints cannot corrupt the result
    try:
        result = execute(job)
    finally:
        sys.stdout = real_stdout
    real_stdout.write(json.dumps(result.to_dict(), default=str))
    real_stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_worker.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. Split any string literal ruff reports as too long.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/worker.py tests/test_worker.py
git commit -m "feat(core): per-instance worker processes with process-group timeouts"
```

---

### Task 15: The runner

**Files:**
- Create: `src/preflight/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes:
  - `Node` and `Plan` (Task 13);
  - `Job`, `encode_data`, `launch` and `kill_all` (Task 14);
  - `worker_environment` (Task 6);
  - `Status`, `Outcome` and `apply_remedy` (Task 3);
  - `REGISTRY` (Task 4).
- Produces:
  - `NodeResult(node, status, outcome, blocked_by, duration)`;
  - `Launcher = Callable[[Node], Outcome]`;
  - `job_for(node) -> Job` and `default_launcher(node) -> Outcome`;
  - `run_plan(plan, *, jobs=4, launcher=default_launcher) -> dict[str, NodeResult]`, which returns results in dependency order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runner.py
import threading
import time
from pathlib import Path

import pytest

from preflight import runner
from preflight.contract import Contract
from preflight.graph import Node, Plan
from preflight.outcome import Status, fail, ok, outcome


def contract(sections=None):
    return Contract(
        path=Path("dev.toml"),
        root=Path("/tmp"),
        scope="environment",
        environment="dev",
        identity_data={},
        sections=sections or {},
        placeholders=[],
        inputs={},
    )


CONTRACT = contract()


def node(node_id, *requires, static=None, bound=False, c=CONTRACT, data=None):
    return Node(
        id=node_id,
        check_id="graph.thing",
        key=node_id,
        gates=["g"],
        contract=c,
        data=data or {},
        identity=None,
        requires=list(requires),
        static=static,
        section_bound=bound,
    )


def plan(*nodes):
    return Plan({n.id: n for n in nodes}, [], {"environment": CONTRACT})


def scripted(outcomes):
    calls = []

    def launch(n):
        calls.append(n.id)
        return outcomes[n.id]

    launch.calls = calls
    return launch


def test_dependents_of_a_failure_are_blocked_and_not_run():
    launch = scripted({"a": outcome(fail(do="x")), "b": outcome(ok()), "c": outcome(ok())})
    results = runner.run_plan(plan(node("a"), node("b", "a"), node("c", "b")), launcher=launch)
    assert [r.status for r in results.values()] == [Status.FAIL, Status.BLOCKED, Status.BLOCKED]
    assert results["b"].blocked_by == ["a"]
    assert results["c"].blocked_by == ["b"]
    assert launch.calls == ["a"]


def test_an_advisory_failure_does_not_block():
    launch = scripted({"a": outcome(fail("w", do="x", advisory=True)), "b": outcome(ok())})
    results = runner.run_plan(plan(node("a"), node("b", "a")), launcher=launch)
    assert results["b"].status is Status.OK


def test_static_outcomes_are_not_launched():
    launch = scripted({})
    results = runner.run_plan(
        plan(node("p", static=outcome(fail(do="Fill it."))), node("b", "p")), launcher=launch
    )
    assert [r.status for r in results.values()] == [Status.FAIL, Status.BLOCKED]
    assert launch.calls == []


def test_the_sections_remedy_is_applied():
    c = contract({"s": {"remedy": {"do": "Rerun for {environment}."}}})
    launch = scripted({"s": outcome(fail(do="generic", generic=True))})
    n = node("s", bound=True, c=c, data=c.sections["s"])
    result = runner.run_plan(Plan({"s": n}, [], {"environment": c}), launcher=launch)["s"]
    assert result.outcome.items[0].next_step.do == "Rerun for dev."


def test_no_more_than_jobs_run_at_once():
    active = 0
    peak = 0
    lock = threading.Lock()

    def launch(n):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return outcome(ok())

    runner.run_plan(plan(*[node(f"n{i}") for i in range(6)]), jobs=2, launcher=launch)
    assert peak == 2


def test_an_interrupt_kills_the_workers(monkeypatch):
    killed = []
    monkeypatch.setattr(runner, "kill_all", lambda: killed.append(True))

    def launch(n):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_plan(plan(node("a")), launcher=launch)
    assert killed == [True]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL (`ImportError: cannot import name 'runner'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/runner.py
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
from preflight.outcome import Outcome, Status, apply_remedy
from preflight.worker import Job, encode_data, kill_all, launch


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
    check = REGISTRY[node.check_id]
    env = worker_environment(
        os.environ,
        _identity(node),
        keep_aws=check.ambient,
        bin_dir=str(Path(sys.executable).parent),
    )
    return launch(job_for(node), env=env, timeout=node.timeout)


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
        except KeyboardInterrupt:
            kill_all()
            raise
    return {node.id: results[node.id] for node in order}
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_runner.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/runner.py tests/test_runner.py
git commit -m "feat(core): run a plan in dependency order with blocked propagation"
```

---

### Task 16: Rendering — worklist, JSON, JUnit

**Files:**
- Create: `src/preflight/render.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `NodeResult` (Task 15), `Item`, `NextStep` and `Status` (Task 3), and `Node` (Task 13).
- Produces:
  - `OpenStep(item_id, node_id, status, next_step, also=(), unblocks=())`;
  - `item_id(node_id, item) -> str`;
  - `open_steps(results) -> list[OpenStep]`;
  - `warnings(results) -> list[tuple[str, NextStep|None]]`;
  - `render_results(results, *, color) -> list[str]`;
  - `render_steps(steps, *, first_is_next=True) -> list[str]`;
  - `render_check(title, results, *, color) -> str`;
  - `instance_json(result) -> dict` and `run_json(gate, environment, results) -> dict`;
  - `report_json(*, command, exit_code, started, finished, inputs, runs, steps, inputs_changed) -> dict`;
  - `write_json(path, data)`;
  - `to_junit(name, results) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render.py
import stat
from pathlib import Path
from xml.etree import ElementTree

from preflight.contract import Contract
from preflight.graph import Node
from preflight.outcome import Status, error, fail, ok, outcome, pending
from preflight.render import (
    instance_json,
    open_steps,
    render_check,
    report_json,
    to_junit,
    write_json,
)
from preflight.runner import NodeResult

CONTRACT = Contract(
    path=Path("dev.toml"), root=Path("/tmp"), scope="environment", environment="dev",
    identity_data={}, sections={}, placeholders=[], inputs={},
)


def result(node_id, out=None, blocked_by=()):
    node = Node(
        id=node_id, check_id=node_id.split("[")[0], key=node_id, gates=["delegation"],
        contract=CONTRACT, data={}, identity=None, section_bound=False,
    )
    status = Status.BLOCKED if out is None else out.status
    return NodeResult(node, status, out, list(blocked_by), 0.5)


RESULTS = {
    r.node.id: r
    for r in [
        result("aws.session[admin]", outcome(ok())),
        result(
            "dns.delegated[delegation]",
            outcome(
                fail(
                    "app",
                    do="In Administration, set NS app.tellabs.dev to exactly these servers:",
                    paste="app.tellabs.dev. NS ns-1.example.\napp.tellabs.dev. NS ns-2.example.",
                    wait="up to 15 minutes",
                    ref="README, DNS step 1",
                ),
                ok("api"),
            ),
        ),
        result("acm.issued[root_certificate]", blocked_by=["dns.delegated[delegation]"]),
        result("dns.cname[aliases]", outcome(fail("vault", do="Add CNAME vault.tellabs.dev.", advisory=True))),
    ]
}

EXPECTED = """preflight check delegation (dev)

  ok       aws.session[admin]
  FAIL     dns.delegated[delegation]:app
  ok       dns.delegated[delegation]:api
  blocked  acm.issued[root_certificate]  (waits on: dns.delegated[delegation])
  warn     dns.cname[aliases]:vault

Open steps:
> NEXT  dns.delegated[delegation]:app
        In Administration, set NS app.tellabs.dev to exactly these servers:
          app.tellabs.dev. NS ns-1.example.
          app.tellabs.dev. NS ns-2.example.
        wait: up to 15 minutes
        see: README, DNS step 1
        then unblocks: acm.issued[root_certificate]

Warnings:
  dns.cname[aliases]:vault: Add CNAME vault.tellabs.dev.
"""


def test_the_worklist():
    assert render_check("preflight check delegation (dev)", RESULTS, color=False) == EXPECTED


def test_color_only_when_asked():
    assert "\033[31mFAIL" in render_check("t", RESULTS, color=True)
    assert "\033" not in render_check("t", RESULTS, color=False)


def test_nothing_open():
    text = render_check("t", {"a": result("a[x]", outcome(ok()))}, color=False)
    assert text.endswith("Nothing open.\n")


def test_pending_comes_after_failures_and_identical_steps_merge():
    results = {
        r.node.id: r
        for r in [
            result("a[x]", outcome(pending(wait="an hour"))),
            result("b[x]", outcome(fail("1", do="Same."), fail("2", do="Same."))),
        ]
    }
    steps = open_steps(results)
    assert [s.item_id for s in steps] == ["b[x]:1", "a[x]"]
    assert steps[0].also == ("b[x]:2",)


def test_json_items_carry_the_public_next_step_shape():
    data = instance_json(RESULTS["dns.delegated[delegation]"])
    assert data["status"] == "fail"
    assert data["gate"] == "delegation"
    assert set(data["items"][0]["next_step"]) == {"do", "paste", "wait", "ref"}
    report = report_json(
        command="check", exit_code=1, started="s", finished="f", inputs={"a": "1"},
        runs=[], steps=open_steps(RESULTS), inputs_changed=[],
    )
    assert report["next"] == "dns.delegated[delegation]:app"
    assert report["inputs"] == [{"path": "a", "sha256": "1"}]


def test_json_files_are_private(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"a": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == '{\n  "a": 1\n}\n'


def test_junit_never_skips():
    extra = {"e[x]": result("e[x]", outcome(error(do="Sign in.", error_type="Timeout")))}
    text = to_junit("dev", {**RESULTS, **extra})
    suite = ElementTree.fromstring(text.split("?>", 1)[1])
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("6", "2", "1")
    assert "skipped" not in text
    vault = suite.find("testcase[@name='dns.cname[aliases]:vault']")
    assert vault.find("system-out").text == "warning: Add CNAME vault.tellabs.dev."
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL (`ModuleNotFoundError: preflight.render`).

- [ ] **Step 3: Implement**

```python
# src/preflight/render.py
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
                    existing, also=existing.also + (step.item_id,), unblocks=existing.unblocks + extra
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
                    case, "failure", type="blocked", message=f"waits on {', '.join(result.blocked_by)}"
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
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_render.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. If `ruff format --check` wants to reflow the compact test fixtures, run `uv run ruff format tests/test_render.py`, confirm `EXPECTED` did not change, and rerun.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/render.py tests/test_render.py
git commit -m "feat(core): worklist, JSON and JUnit rendering"
```

---
### Task 17: CLI — `check` and `validate`

**Files:**
- Create: `src/preflight/cli.py`, `src/preflight/__main__.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `load_contract`, `repo_root` and `ContractError` (Task 7);
  - `load_gate_file`, `load_closure`, `load_directory` and `GateError` (Task 8);
  - `build_plan` and `GraphError` (Task 13);
  - `run_plan` (Task 15);
  - `open_steps`, `render_check`, `run_json`, `report_json`, `write_json` and `to_junit` (Task 16).
- Produces:
  - `main(argv=None) -> int` and `build_parser()`;
  - `cmd_check(args)` and `cmd_validate(args)`;
  - `Invalid(problems)`;
  - `_load_contracts(dir) -> (repository | None, environments)`, `_validate_all(gates, repository, environments)` and `_with_requirements(gates, chosen)`.

  Task 18 reuses these private helpers.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json
import stat

import pytest
from conftest import write

from preflight import cli

CHECKS = """
from pathlib import Path

from preflight import Section, check, fail, ok, outcome


class Flag(Section):
    good: bool = True


class Touch(Section):
    file: str
    marker: str = ""


@check("sample.flag", section=Flag)
def flag(ctx, s):
    return outcome(ok() if s.good else fail(do="Flip the flag."))


@check("sample.touch", section=Touch)
def touch(ctx, s):
    with open(Path(ctx.root, s.file), "a") as handle:
        handle.write("# touched\\n")
    return outcome(ok())
"""

GATE = """
from preflight import Gate
from consumer.checks.flags import flag, touch

gate = Gate({name!r}, checks=[{checks}], requires={requires!r}, scope={scope!r})
"""

DEV = """
schema_version = 1
environment = "dev"

[flags]
good = {good}

[touch]
file = "envs/dev.tfvars"
marker = {{ tfvars = "envs/dev.tfvars", key = "marker" }}
"""


def gate(repo, name, checks, requires=(), scope="environment"):
    write(
        repo,
        f"preflight/gates/{name}.py",
        GATE.format(name=name, checks=checks, requires=list(requires), scope=scope),
    )
    return repo / "preflight" / "gates" / f"{name}.py"


def consumer(repo, good="true"):
    write(repo, "preflight/checks/flags.py", CHECKS)
    write(repo, "envs/dev.tfvars", 'marker = "m"\n')
    write(repo, "preflight/contracts/dev.toml", DEV.format(good=good))
    gate(repo, "touch", 'touch("touch")')
    return gate(repo, "g", 'flag("flags")'), repo / "preflight" / "contracts" / "dev.toml"


def test_check_passes(repo, capsys):
    gate_path, contract = consumer(repo)
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 0
    assert capsys.readouterr().out.endswith("Nothing open.\n")


def test_check_fails_and_names_the_next_step(repo, capsys):
    gate_path, contract = consumer(repo, good="false")
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 1
    out = capsys.readouterr().out
    assert "> NEXT  sample.flag[flags]\n        Flip the flag.\n" in out
    assert "\033" not in out  # piped output carries no colour


def test_an_invalid_contract_is_exit_2(repo, capsys):
    _, contract = consumer(repo)
    bad = gate(repo, "bad", 'flag("nope")')
    assert cli.main(["check", str(bad), "--contract", str(contract)]) == 2
    assert "no section [nope]" in capsys.readouterr().err


def test_a_broken_gate_file_is_exit_2_naming_it(repo, capsys):
    _, contract = consumer(repo)
    broken = write(repo, "preflight/gates/broken.py", "gate = (\n")
    assert cli.main(["check", str(broken), "--contract", str(contract)]) == 2
    assert "broken.py" in capsys.readouterr().err


def test_json_and_junit_reports(repo, tmp_path_factory):
    gate_path, contract = consumer(repo, good="false")
    out = tmp_path_factory.mktemp("out")
    code = cli.main(
        ["check", str(gate_path), "--contract", str(contract),
         "--json", str(out / "r.json"), "--junit", str(out / "r.xml")]
    )
    assert code == 1
    data = json.loads((out / "r.json").read_text())
    assert (data["exit_code"], data["next"]) == (1, "sample.flag[flags]")
    assert data["runs"][0]["gate"] == "g"
    assert stat.S_IMODE((out / "r.json").stat().st_mode) == 0o600
    assert "<failure" in (out / "r.xml").read_text()


def test_relative_paths_from_another_directory(repo, monkeypatch):
    consumer(repo)
    monkeypatch.chdir(repo / "preflight")
    assert cli.main(["check", "gates/g.py", "--contract", "contracts/dev.toml"]) == 0


def test_inputs_changed_during_the_run_block(repo, capsys):
    _, contract = consumer(repo)
    touch = repo / "preflight" / "gates" / "touch.py"
    assert cli.main(["check", str(touch), "--contract", str(contract)]) == 1
    assert "Inputs changed during the run: envs/dev.tfvars" in capsys.readouterr().out


def test_a_repository_gate_needs_repo_toml(repo, capsys):
    _, contract = consumer(repo)
    gate(repo, "repo_gate", 'flag("flags")', scope="repository")
    needs = gate(repo, "needs", 'flag("flags")', requires=["repo_gate"])
    assert cli.main(["check", str(needs), "--contract", str(contract)]) == 2
    assert "repo.toml" in capsys.readouterr().err


def test_an_internal_error_is_exit_3(repo, monkeypatch, capsys):
    gate_path, contract = consumer(repo)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "run_plan", boom)
    assert cli.main(["check", str(gate_path), "--contract", str(contract)]) == 3
    assert "internal error (RuntimeError" in capsys.readouterr().err


def test_usage_errors_are_exit_2():
    with pytest.raises(SystemExit) as caught:
        cli.main(["check", "gates/g.py"])
    assert caught.value.code == 2


def test_validate(repo, monkeypatch, capsys):
    consumer(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["validate"]) == 0
    assert "valid" in capsys.readouterr().out
    contract = repo / "preflight" / "contracts" / "dev.toml"
    contract.write_text(contract.read_text().replace("good = true", "good = true\nstray = 1"))
    assert cli.main(["validate"]) == 2
    assert "[flags].stray is used by no check" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL (`ImportError: cannot import name 'cli'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/cli.py
"""preflight check | status | validate.

`check` runs one gate (and the gates it requires) against a contract and exits 0 only when
every blocking item is ok. A consumer's script calls it first and stops on anything else."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from preflight.contract import Contract, ContractError, load_contract, repo_root
from preflight.gate import Gate, GateError, load_closure, load_directory, load_gate_file
from preflight.graph import GraphError, build_plan
from preflight.outcome import Status
from preflight.render import (
    open_steps,
    render_check,
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
        contracts["repository"] = load_contract(path)
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
    status.set_defaults(handler=lambda args: cmd_status(args))

    validate = commands.add_parser("validate", help="load every gate and contract; observe nothing")
    validate.add_argument("--gates", type=Path)
    validate.add_argument("--contracts", type=Path)
    validate.set_defaults(handler=cmd_validate)
    return parser


def cmd_status(args: argparse.Namespace) -> int:
    raise NotImplementedError  # Task 18


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("preflight: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # a bug in preflight itself
        print(f"preflight: internal error ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# src/preflight/__main__.py
from preflight.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_cli.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/cli.py src/preflight/__main__.py tests/test_cli.py
git commit -m "feat(cli): preflight check and preflight validate"
```

---

### Task 18: CLI — `status`

**Files:**
- Modify: `src/preflight/cli.py` (replace the `cmd_status` stub), `src/preflight/render.py` (add `render_status`)
- Create: `tests/test_status.py`

**Interfaces:**
- Consumes: everything from Task 17, plus `render_steps` (Task 16).
- Produces:
  - `render_status(sections: list[tuple[str, list[tuple[str, str, list[str]]]]], main_steps, also_steps) -> str`;
  - `cmd_status(args)`, which returns 0 only when every milestone gate is satisfied in every run.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_status.py
from conftest import write
from test_cli import CHECKS, gate

from preflight import cli

REPO_TOML = 'schema_version = 1\nscope = "repository"\n\n[repo_flag]\ngood = true\n'
ENV_TOML = """schema_version = 1
environment = "{env}"

[boot]
good = {boot}

[later_flag]
good = {later}

[entry]
good = true
"""

EXPECTED = """preflight status

repository
  satisfied  repository

dev
  open (1)   bootstrap
  waiting    later  (waits on bootstrap)

prod
  satisfied  bootstrap
  satisfied  later

Open steps:
> NEXT  dev: sample.flag[boot]
        Flip the flag.

Also open, in gates still waiting:
  1.    dev: sample.flag[later_flag]
        Flip the flag.
"""


def consumer(repo, dev_boot="false"):
    write(repo, "preflight/checks/flags.py", CHECKS)
    write(repo, "preflight/contracts/repo.toml", REPO_TOML)
    write(repo, "preflight/contracts/dev.toml", ENV_TOML.format(env="dev", boot=dev_boot, later="false"))
    write(repo, "preflight/contracts/prod.toml", ENV_TOML.format(env="prod", boot="true", later="true"))
    gate(repo, "repository", 'flag("repo_flag")', scope="repository")
    gate(repo, "bootstrap", 'flag("boot")', requires=["repository"])
    gate(repo, "later", 'flag("later_flag")', requires=["bootstrap"])
    write(
        repo,
        "preflight/gates/bootstrap_entry.py",
        "from preflight import Gate\nfrom consumer.checks.flags import flag\n\n"
        'gate = Gate("bootstrap_entry", checks=[flag("entry")], requires=["repository"], '
        'guards="scripts/bootstrap.sh")\n',
    )


def test_status_shows_every_environment_and_one_next_step(repo, monkeypatch, capsys):
    consumer(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert out == EXPECTED
    assert "bootstrap_entry" not in out


def test_status_passes_when_everything_is_satisfied(repo, monkeypatch, capsys):
    consumer(repo, dev_boot="true")
    contract = repo / "preflight" / "contracts" / "dev.toml"
    contract.write_text(contract.read_text().replace("[later_flag]\ngood = false", "[later_flag]\ngood = true"))
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out.endswith("Nothing open.\n")


def test_status_without_contracts_is_exit_2(repo, monkeypatch, capsys):
    consumer(repo)
    for path in (repo / "preflight" / "contracts").glob("*.toml"):
        path.unlink()
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 2
    assert "holds no contracts" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_status.py -v`
Expected: FAIL (the stub `cmd_status` raises `NotImplementedError`, which `main` turns into exit 3).

- [ ] **Step 3: Add `render_status` to `src/preflight/render.py`**

Append:

```python
def render_status(
    sections: Sequence[tuple[str, Sequence[tuple[str, str, Sequence[str]]]]],
    main_steps: Sequence[OpenStep],
    also_steps: Sequence[OpenStep],
) -> str:
    lines = ["preflight status"]
    for label, rows in sections:
        lines += ["", label]
        for state, name, waits in rows:
            suffix = f"  (waits on {', '.join(waits)})" if waits else ""
            lines.append(f"  {state:<10} {name}{suffix}")
    lines.append("")
    if main_steps:
        lines += ["Open steps:", *render_steps(main_steps)]
    elif also_steps:
        lines.append("Nothing open outside gates still waiting.")
    else:
        lines.append("Nothing open.")
    if also_steps:
        lines += ["", "Also open, in gates still waiting:"]
        lines += render_steps(also_steps, first_is_next=False)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Replace the `cmd_status` stub in `src/preflight/cli.py`**

Add `from dataclasses import replace` to the imports, and `render_status` to the `preflight.render` import list. Then replace the stub with:

```python
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
```

Also change `status.set_defaults(handler=lambda args: cmd_status(args))` in `build_parser` to `status.set_defaults(handler=cmd_status)`, and move `build_parser` below `cmd_status` if ruff or readability calls for it.

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_status.py tests/test_cli.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/preflight/cli.py src/preflight/render.py tests/test_status.py
git commit -m "feat(cli): preflight status across every environment"
```

---

### Task 19: Catalog — `acm.issued`

**Files:**
- Create: `src/preflight/catalog/acm.py`, `tests/test_catalog_acm.py`

**Interfaces:**
- Consumes:
  - `Context.aws_module` and `Context.dns.cname` (Tasks 9 and 10);
  - `norm` and `DnsUnavailable` (Task 9);
  - `session_for` (Task 4).
- Produces:
  - `CertificateSection(identity, certificate_arn)`;
  - the check `acm.issued`, which reads the certificate through `community.aws.acm_certificate_info`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_acm.py
from fakes import FakeAnsibleHost, FakeDns, make_ctx
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import acm
from preflight.dnsclient import DnsUnavailable
from preflight.outcome import Status

ARN = "arn:aws:acm:us-east-1:111111111111:certificate/abc"
SECTION = acm.CertificateSection(identity="admin", certificate_arn=ARN)
RECORD = {"name": "_x.tellabs.dev.", "type": "CNAME", "value": "_y.acm-validations.aws."}


def certificate(status, records=(RECORD,)):
    options = [{"domain_name": "*.tellabs.dev", "resource_record": r} for r in records]
    return FakeAnsibleHost(
        {"community.aws.acm_certificate_info": {"certificates": [{"status": status, "domain_validation_options": options}]}}
    )


def observe(tmp_path, ansible, dns=None):
    return acm.issued.observe(make_ctx(tmp_path, ansible=ansible, dns=dns or FakeDns()), SECTION)


def test_issued(tmp_path):
    ansible = certificate("ISSUED")
    assert observe(tmp_path, ansible).status is Status.OK
    assert ansible.calls[0] == (
        "community.aws.acm_certificate_info",
        {"certificate_arn": ARN, "region": "us-east-1", "profile": "sandbox"},
    )


def test_pending_without_the_cname_is_the_operators_step(tmp_path):
    item = observe(tmp_path, certificate("PENDING_VALIDATION")).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "_x.tellabs.dev. CNAME _y.acm-validations.aws."


def test_pending_with_the_cname_visible_is_waiting(tmp_path):
    dns = FakeDns(cnames={"_x.tellabs.dev.": ["_y.acm-validations.aws"]})
    assert observe(tmp_path, certificate("PENDING_VALIDATION"), dns).status is Status.PENDING


def test_pending_before_acm_publishes_records_is_waiting(tmp_path):
    assert observe(tmp_path, certificate("PENDING_VALIDATION", records=())).status is Status.PENDING


def test_a_failed_certificate_asks_for_a_new_one(tmp_path):
    item = observe(tmp_path, certificate("VALIDATION_TIMED_OUT")).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.generic is True


def test_errors(tmp_path):
    failing = FakeAnsibleHost({"community.aws.acm_certificate_info": AnsibleException({"failed": True})})
    assert observe(tmp_path, failing).items[0].error_type == "AnsibleException"
    dns = FakeDns(cnames={"_x.tellabs.dev.": DnsUnavailable("x")})
    assert observe(tmp_path, certificate("PENDING_VALIDATION"), dns).items[0].error_type == "DnsUnavailable"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_acm.py -v`
Expected: FAIL (`ImportError: cannot import name 'acm'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/acm.py
"""A certificate the operator validates by hand: issued, or exactly which record is missing.
Pending counts as the operator's step until the validation CNAME is visible."""

from __future__ import annotations

from typing import Any

from preflight.check import IdentityRef, Section, check, session_for
from preflight.dnsclient import DnsUnavailable, norm
from preflight.outcome import Outcome, error, fail, ok, outcome, pending


class CertificateSection(Section):
    identity: IdentityRef
    certificate_arn: str


def _validation_records(certificate: dict[str, Any]) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for option in certificate.get("domain_validation_options") or []:
        record = option.get("resource_record")
        if record and record.get("type", "CNAME") == "CNAME":
            records[norm(record["name"])] = {"name": record["name"], "value": record["value"]}
    return list(records.values())


@check("acm.issued", section=CertificateSection, requires=[session_for("identity")])
def issued(ctx, s: CertificateSection) -> Outcome:
    try:
        info = ctx.aws_module(
            "community.aws.acm_certificate_info", {"certificate_arn": s.certificate_arn}
        )
    except Exception as exc:
        return outcome(
            error(
                do=(
                    "Could not describe the certificate; check that profile "
                    f"{ctx.identity.profile} may call acm:DescribeCertificate."
                ),
                error_type=type(exc).__name__,
            )
        )
    certificates = info.get("certificates") or []
    if not certificates:
        return outcome(
            fail(do=f"No certificate {s.certificate_arn} exists in {ctx.identity.region}.", generic=True)
        )
    status = certificates[0].get("status", "UNKNOWN")
    if status == "ISSUED":
        return outcome(ok(observed=status))
    if status != "PENDING_VALIDATION":
        return outcome(
            fail(do=f"The certificate is {status}; request a new one.", observed=status, generic=True)
        )
    records = _validation_records(certificates[0])
    if not records:
        return outcome(
            pending(wait="ACM has not published the validation record yet; recheck in a minute.")
        )
    try:
        missing = [r for r in records if norm(r["value"]) not in ctx.dns.cname(r["name"])]
    except DnsUnavailable:
        return outcome(
            error(
                do="Could not look up the certificate's validation records; check DNS, then rerun.",
                error_type="DnsUnavailable",
            )
        )
    if missing:
        return outcome(
            fail(
                do="In Administration, add the certificate's validation record(s):",
                paste="\n".join(f"{norm(r['name'])}. CNAME {norm(r['value'])}." for r in missing),
                observed=status,
            )
        )
    return outcome(
        pending(
            wait=(
                "ACM usually issues within minutes of seeing the record, and gives up 72 hours "
                "after the request; recheck later."
            ),
            observed=status,
        )
    )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_acm.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/acm.py tests/test_catalog_acm.py
git commit -m "feat(catalog): acm.issued names the missing validation record"
```

---

### Task 20: Catalog — `tofu.plan_clean`

**Files:**
- Create: `src/preflight/catalog/tofu.py`, `tests/test_catalog_tofu.py`

**Interfaces:**
- Consumes: `Context.host` and `Context.path` (Task 10), and `session_for` (Task 4).
- Produces:
  - `PlanSection(dir, var_files=[], identity=None)`;
  - the check `tofu.plan_clean` (timeout 600);
  - `first_error(text) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_tofu.py
from fakes import FakeHost, FakeResult, make_ctx

from preflight.catalog import tofu
from preflight.outcome import Status

SECTION = tofu.PlanSection(dir="stacks/bootstrap", var_files=["envs/dev.tfvars"], identity="admin")


def host(init=FakeResult(0), plan=FakeResult(0), files=None):
    return FakeHost([(" init -input", init), (" plan -lock", plan)], files=files)


def observe(tmp_path, fake):
    return tofu.plan_clean.observe(make_ctx(tmp_path, host=fake), SECTION)


def test_a_clean_plan_is_read_only(tmp_path):
    fake = host()
    assert observe(tmp_path, fake).status is Status.OK
    init, plan = fake.commands
    assert "TF_DATA_DIR=" in init and "-lockfile=readonly" in init and "-reconfigure" in init
    assert f"-var-file={tmp_path}/envs/dev.tfvars" in init
    assert "-lock=false" in plan and "-detailed-exitcode" in plan
    assert f"-chdir={tmp_path}/stacks/bootstrap" in plan


def test_pending_changes_fail_with_a_generic_step(tmp_path):
    item = observe(tmp_path, host(plan=FakeResult(2))).items[0]
    assert (item.status, item.next_step.generic) == (Status.FAIL, True)


def test_an_error_reports_only_the_first_error_line(tmp_path):
    stderr = "│ Error: No valid credential sources found\n│ \n│ detail with SECRET\n"
    item = observe(tmp_path, host(plan=FakeResult(1, "", stderr))).items[0]
    assert item.status is Status.ERROR
    assert item.observed == "Error: No valid credential sources found"


def test_leftover_local_state_is_refused(tmp_path):
    fake = host(files={f"{tmp_path}/stacks/bootstrap/backend.tf.off": ""})
    item = observe(tmp_path, fake).items[0]
    assert item.error_type == "LeftoverState"
    assert fake.commands == []


def test_a_missing_tofu_binary(tmp_path):
    assert observe(tmp_path, host(init=FakeResult(127))).items[0].error_type == "MissingTool"


def test_first_error():
    assert tofu.first_error("x\nError: boom\n") == "Error: boom"
    assert tofu.first_error("nothing") is None
    assert len(tofu.first_error("Error: " + "a" * 500)) == 200
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_tofu.py -v`
Expected: FAIL (`ImportError: cannot import name 'tofu'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/tofu.py
"""A clean plan: exit 0 from `tofu plan -detailed-exitcode`, run read-only (no state lock, no
lockfile writes, a throwaway data directory)."""

from __future__ import annotations

import shutil
import tempfile

from preflight.check import IdentityRef, Section, check, session_for
from preflight.outcome import Outcome, error, fail, ok, outcome

LEFTOVERS = ("backend.tf.off", "terraform.tfstate")


class PlanSection(Section):
    dir: str
    var_files: list[str] = []
    identity: IdentityRef | None = None


def first_error(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip().lstrip("│").strip()
        if stripped.startswith("Error:"):
            return stripped[:200]
    return None


@check("tofu.plan_clean", section=PlanSection, requires=[session_for("identity")], timeout=600)
def plan_clean(ctx, s: PlanSection) -> Outcome:
    directory = ctx.path(s.dir)
    leftovers = [name for name in LEFTOVERS if ctx.host.file(str(directory / name)).exists]
    if leftovers:
        return outcome(
            error(
                do=(
                    f"{s.dir} holds {', '.join(leftovers)} from an interrupted run; a plan there "
                    "would read the wrong state. Finish or clean up that run first."
                ),
                error_type="LeftoverState",
            )
        )
    var_args = [f"-var-file={ctx.path(f)}" for f in s.var_files]
    extra = " %s" * len(var_args)
    data_dir = tempfile.mkdtemp(prefix="preflight-tofu-")
    try:
        init = ctx.host.run(
            "env TF_DATA_DIR=%s tofu -chdir=%s init -input=false -no-color -reconfigure "
            "-lockfile=readonly" + extra,
            data_dir,
            str(directory),
            *var_args,
        )
        if init.rc == 127:
            return outcome(error(do="Install OpenTofu (tofu).", error_type="MissingTool"))
        if init.rc != 0:
            return outcome(
                error(
                    do=f"`tofu init` failed in {s.dir}; run it there to see why.",
                    observed=first_error(init.stderr + init.stdout),
                    error_type="TofuInit",
                )
            )
        plan = ctx.host.run(
            "env TF_DATA_DIR=%s tofu -chdir=%s plan -lock=false -input=false -no-color "
            "-detailed-exitcode" + extra,
            data_dir,
            str(directory),
            *var_args,
        )
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    if plan.rc == 0:
        return outcome(ok())
    if plan.rc == 2:
        return outcome(fail(do=f"{s.dir} has changes that are not applied.", generic=True))
    return outcome(
        error(
            do=f"`tofu plan` failed in {s.dir}; run it there to see why.",
            observed=first_error(plan.stderr + plan.stdout),
            error_type="TofuPlan",
        )
    )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_tofu.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/tofu.py tests/test_catalog_tofu.py
git commit -m "feat(catalog): tofu.plan_clean, read-only"
```

---

### Task 21: Catalog — DNS

**Files:**
- Create: `src/preflight/catalog/dns.py`, `tests/test_catalog_dns.py`

**Interfaces:**
- Consumes: `Context.dns` (Task 10), and `Referral`, `DnsUnavailable` and `norm` (Task 9).
- Produces:
  - the sections `DelegationSection(root, zones, name_servers)`, `UndelegatedSection(root, zones=[])`, `CnameSection(records: list[CnameRecord(name, target, advisory=False)])` and `CaaSection(domain, issuers)`;
  - the checks `dns.delegated`, `dns.undelegated`, `dns.cname` and `dns.caa`;
  - `ns_block(name, servers) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_dns.py
from fakes import FakeDns, make_ctx

from preflight.catalog import dns
from preflight.dnsclient import DnsUnavailable, Referral
from preflight.outcome import Status

SERVERS = ["ns-2.example.", "ns-1.example."]


def delegation(zones=("app",), servers=None):
    return dns.DelegationSection(
        root="tellabs.dev", zones=list(zones), name_servers={"app": servers or SERVERS}
    )


def run(check, section, tmp_path, fake):
    return check.observe(make_ctx(tmp_path, dns=fake), section)


def test_delegated_exactly(tmp_path):
    fake = FakeDns(referrals={"app.tellabs.dev": Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))})
    assert run(dns.delegated, delegation(), tmp_path, fake).status is Status.OK


def test_a_stale_extra_server_fails_with_the_exact_block(tmp_path):
    fake = FakeDns(
        referrals={"app.tellabs.dev": Referral("referral", frozenset({"ns-1.example", "ns-2.example", "old.example"}))}
    )
    item = run(dns.delegated, delegation(), tmp_path, fake).items[0]
    assert item.key == "app"
    assert item.status is Status.FAIL
    assert item.next_step.paste == "app.tellabs.dev. NS ns-1.example.\napp.tellabs.dev. NS ns-2.example."
    assert "replacing any others" in item.next_step.do


def test_no_delegation_and_no_zone_and_unavailable(tmp_path):
    fake = FakeDns(referrals={"app.tellabs.dev": Referral("none", frozenset()), "api.tellabs.dev": DnsUnavailable("x")})
    section = dns.DelegationSection(root="tellabs.dev", zones=["app", "api", "new"], name_servers={"app": SERVERS, "api": SERVERS})
    items = {i.key: i for i in run(dns.delegated, section, tmp_path, fake).items}
    assert items["app"].status is Status.FAIL
    assert items["api"].status is Status.ERROR
    assert (items["new"].status, items["new"].next_step.generic) == (Status.FAIL, True)


def test_no_zones_is_ok(tmp_path):
    assert run(dns.delegated, delegation(zones=()), tmp_path, FakeDns()).status is Status.OK


def test_undelegated(tmp_path):
    section = dns.UndelegatedSection(root="tellabs.dev", zones=["old", "gone", "lame"])
    fake = FakeDns(
        referrals={
            "old.tellabs.dev": Referral("referral", frozenset({"ns-1.example"})),
            "gone.tellabs.dev": Referral("none", frozenset()),
            "lame.tellabs.dev": DnsUnavailable("SERVFAIL"),
        }
    )
    items = {i.key: i for i in run(dns.undelegated, section, tmp_path, fake).items}
    assert items["old"].status is Status.FAIL
    assert items["gone"].status is Status.OK
    assert items["lame"].status is Status.ERROR  # never ok when nothing answered


def test_cname_with_an_advisory_record(tmp_path):
    section = dns.CnameSection(
        records=[
            {"name": "vault.tellabs.dev", "target": "vault.app.tellabs.dev"},
            {"name": "wiki.tellabs.dev", "target": "wiki.app.tellabs.dev", "advisory": True},
        ]
    )
    fake = FakeDns(cnames={"vault.tellabs.dev": ["vault.app.tellabs.dev"]})
    result = run(dns.cname, section, tmp_path, fake)
    items = {i.key: i for i in result.items}
    assert items["vault.tellabs.dev"].status is Status.OK
    assert items["wiki.tellabs.dev"].advisory is True
    assert items["wiki.tellabs.dev"].next_step.paste == "wiki.tellabs.dev. CNAME wiki.app.tellabs.dev."
    assert result.status is Status.OK


def test_caa(tmp_path):
    section = dns.CaaSection(domain="tellabs.dev", issuers=["amazon.com", "amazontrust.com"])
    fake = FakeDns(caa={"tellabs.dev": ['0 issue "amazon.com"', '0 iodef "mailto:x@y"']})
    item = run(dns.caa, section, tmp_path, fake).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == 'tellabs.dev. CAA 0 issue "amazontrust.com"'
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_dns.py -v`
Expected: FAIL (`ImportError: cannot import name 'dns'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/dns.py
"""DNS the operator manages by hand: delegations held by the parent zone, CNAMEs, and CAA.
A lookup nobody answered is an error, never an ok."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from preflight.check import Section, check
from preflight.dnsclient import DnsUnavailable, norm
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

Domain = Annotated[
    str, StringConstraints(pattern=r"^([A-Za-z0-9_]([A-Za-z0-9_-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\.?$")
]
Label = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")]
NEGATIVE_CACHE = (
    "resolvers may keep the old answer for up to the zone's negative-cache TTL "
    "(its SOA minimum) after a fix"
)


class DelegationSection(Section):
    root: Domain
    zones: list[Label]
    name_servers: dict[str, list[str]]


class UndelegatedSection(Section):
    root: Domain
    zones: list[Label] = []


class CnameRecord(BaseModel):
    name: Domain
    target: Domain
    advisory: bool = False


class CnameSection(Section):
    records: list[CnameRecord]


class CaaSection(Section):
    domain: Domain
    issuers: list[str]


def ns_block(name: str, servers) -> str:
    return "\n".join(f"{name}. NS {server}." for server in sorted(servers))


def _all(items: list[Item]) -> Outcome:
    return outcome(*items) if items else outcome(ok(observed="nothing to check"))


def _unavailable(key: str | None, parent: str, advisory: bool = False) -> Item:
    return error(
        key,
        do=f"No name server for {parent} gave a usable answer; check your network and DNS, then rerun.",
        error_type="DnsUnavailable",
        advisory=advisory,
    )


@check("dns.delegated", section=DelegationSection)
def delegated(ctx, s: DelegationSection) -> Outcome:
    root = norm(s.root)
    items = []
    for prefix in s.zones:
        name = f"{prefix}.{root}"
        expected = {norm(server) for server in s.name_servers.get(prefix, [])}
        if not expected:
            items.append(
                fail(prefix, do=f"{name} has no zone yet (no name servers recorded for it).", generic=True)
            )
            continue
        try:
            referral = ctx.dns.referral(name, root)
        except DnsUnavailable:
            items.append(_unavailable(prefix, root))
            continue
        if referral.kind == "referral" and set(referral.servers) == expected:
            items.append(ok(prefix, observed=sorted(referral.servers)))
            continue
        items.append(
            fail(
                prefix,
                do=(
                    f"In Administration, set the NS record for {name} in {root} to exactly "
                    "these servers, replacing any others:"
                ),
                paste=ns_block(name, expected),
                observed=sorted(referral.servers),
            )
        )
    return _all(items)


@check("dns.undelegated", section=UndelegatedSection)
def undelegated(ctx, s: UndelegatedSection) -> Outcome:
    root = norm(s.root)
    items = []
    for prefix in s.zones:
        name = f"{prefix}.{root}"
        try:
            referral = ctx.dns.referral(name, root)
        except DnsUnavailable:
            items.append(_unavailable(prefix, root))
            continue
        if referral.kind == "none":
            items.append(ok(prefix))
        else:
            items.append(
                fail(
                    prefix,
                    do=(
                        f"In Administration, remove the NS record for {name} from {root}; a "
                        "delegation to a zone being retired is a takeover risk."
                    ),
                    observed=sorted(referral.servers),
                )
            )
    return _all(items)


@check("dns.cname", section=CnameSection)
def cname(ctx, s: CnameSection) -> Outcome:
    items = []
    for record in s.records:
        name, target = norm(record.name), norm(record.target)
        try:
            targets = ctx.dns.cname(name)
        except DnsUnavailable:
            items.append(_unavailable(name, name, record.advisory))
            continue
        if target in targets:
            items.append(ok(name))
        else:
            items.append(
                fail(
                    name,
                    do=f"Add the CNAME {name} → {target}.",
                    paste=f"{name}. CNAME {target}.",
                    wait=NEGATIVE_CACHE,
                    observed=targets,
                    advisory=record.advisory,
                )
            )
    return _all(items)


@check("dns.caa", section=CaaSection)
def caa(ctx, s: CaaSection) -> Outcome:
    domain = norm(s.domain)
    try:
        records = ctx.dns.caa(domain)
    except DnsUnavailable:
        return outcome(_unavailable(None, domain))
    allowed = set()
    for record in records:
        parts = record.split(None, 2)
        if len(parts) == 3 and parts[1].lower() == "issue":
            allowed.add(parts[2].strip('"').split(";")[0].strip().lower())
    missing = [issuer for issuer in s.issuers if issuer.lower() not in allowed]
    if not missing:
        return outcome(ok(observed=sorted(allowed)))
    return outcome(
        fail(
            do=f"Add a CAA record to {domain} allowing {', '.join(missing)}.",
            paste="\n".join(f'{domain}. CAA 0 issue "{issuer}"' for issuer in missing),
            wait=NEGATIVE_CACHE,
            observed=sorted(allowed),
        )
    )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_dns.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/dns.py tests/test_catalog_dns.py
git commit -m "feat(catalog): dns.delegated, undelegated, cname and caa"
```

---

### Task 22: Catalog — GitHub

**Files:**
- Create: `src/preflight/catalog/github.py`, `tests/test_catalog_github.py`

**Interfaces:**
- Consumes: `Context.host` (Task 10).
- Produces:
  - `GhError`, `GhNotFound` and `gh_api(ctx, path) -> Any`;
  - the sections `AuthSection(hostname="github.com")`, `RepoSection(repo, actions_access=None)`, `VariablesSection(repo=None, org=None, variables)`, `EnvironmentsSection(repo, environments)`, `RulesetSection(repo, name, required_checks=[], enforcement="active")`, `SecretNamesSection(repo, environment=None, names)` and `WorkflowSection(repo, workflow, branch=None, event=None)`;
  - the checks `github.auth`, `github.repo`, `github.variables`, `github.environments`, `github.ruleset`, `github.secret_names` and `github.workflow_green`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_github.py
import json

import pytest
from fakes import FakeHost, FakeResult, make_ctx
from pydantic import ValidationError

from preflight.catalog import github
from preflight.outcome import Status

NOT_FOUND = FakeResult(1, "", "gh: Not Found (HTTP 404)")


def gh(*rules):
    return FakeHost([(pattern, FakeResult(0, json.dumps(body)) if not isinstance(body, FakeResult) else body) for pattern, body in rules])


def run(check, section, tmp_path, host):
    return check.observe(make_ctx(tmp_path, host=host), section)


def test_auth(tmp_path):
    section = github.AuthSection()
    assert run(github.auth, section, tmp_path, FakeHost([("gh auth status", FakeResult(0))])).status is Status.OK
    item = run(github.auth, section, tmp_path, FakeHost([("gh auth status", FakeResult(1))])).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "gh auth login")


def test_repo_and_actions_access(tmp_path):
    section = github.RepoSection(repo="tellabsadmin/iac", actions_access="organization")
    host = gh(("actions/permissions/access", {"access_level": "none"}), ("repos/tellabsadmin/iac", {"id": 1}))
    items = {i.key: i for i in run(github.repo, section, tmp_path, host).items}
    assert items["exists"].status is Status.OK
    assert items["actions_access"].status is Status.FAIL
    missing = run(github.repo, section, tmp_path, gh(("repos/tellabsadmin/iac", NOT_FOUND))).items[0]
    assert missing.status is Status.FAIL
    assert missing.next_step.paste == "gh repo create tellabsadmin/iac --private"


def test_variables(tmp_path):
    section = github.VariablesSection(org="tellabsadmin", variables={"AWS_REGION": "us-east-1", "AWS_ACCOUNT_ID_DEV": "111111111111"})
    host = gh(("variables/AWS_REGION", {"value": "us-east-1"}), ("variables/AWS_ACCOUNT_ID_DEV", NOT_FOUND))
    items = {i.key: i for i in run(github.variables, section, tmp_path, host).items}
    assert items["AWS_REGION"].status is Status.OK
    assert items["AWS_ACCOUNT_ID_DEV"].next_step.paste == (
        "gh variable set AWS_ACCOUNT_ID_DEV --org tellabsadmin --body '111111111111'"
    )
    with pytest.raises(ValidationError):
        github.VariablesSection(variables={})


def test_ruleset_required_checks(tmp_path):
    section = github.RulesetSection(repo="tellabsadmin/iac", name="main", required_checks=["validate", "plans", "bootstrap_applied"])
    detail = {
        "enforcement": "active",
        "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "validate"}, {"context": "plans"}]}}],
    }
    host = gh(("rulesets/7", detail), ("rulesets", [{"id": 7, "name": "main"}]))
    items = {i.key: i for i in run(github.ruleset, section, tmp_path, host).items}
    assert items["enforcement"].status is Status.OK
    assert items["required_checks"].status is Status.FAIL
    assert "bootstrap_applied" in items["required_checks"].next_step.do


def test_secret_names_never_read_values(tmp_path):
    section = github.SecretNamesSection(repo="tellabsadmin/iac", environment="platform-dev", names=["SOPS_AGE_KEY"])
    host = gh(("environments/platform-dev/secrets", {"secrets": [{"name": "OTHER"}]}))
    item = run(github.secret_names, section, tmp_path, host).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste.startswith("gh secret set SOPS_AGE_KEY --env platform-dev")


def test_workflow_green(tmp_path):
    section = github.WorkflowSection(repo="tellabsadmin/iac", workflow="platform.yml", branch="main")
    failed = gh(("platform.yml/runs", {"workflow_runs": [{"conclusion": "failure", "html_url": "https://x/1"}]}))
    item = run(github.workflow_green, section, tmp_path, failed).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "https://x/1")
    green = gh(("platform.yml/runs", {"workflow_runs": [{"conclusion": "success", "html_url": "u"}]}))
    assert run(github.workflow_green, section, tmp_path, green).status is Status.OK
    assert "branch=main" in green.commands[0] and "status=completed" in green.commands[0]


def test_other_gh_failures_are_errors(tmp_path):
    section = github.EnvironmentsSection(repo="tellabsadmin/iac", environments=["platform-dev"])
    host = gh(("environments/platform-dev", FakeResult(4, "", "gh: authentication required")))
    item = run(github.environments, section, tmp_path, host).items[0]
    assert (item.status, item.next_step.paste) == (Status.ERROR, "gh auth login")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_github.py -v`
Expected: FAIL (`ImportError: cannot import name 'github'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/github.py
"""GitHub settings the operator makes by hand, read with `gh api`. Secrets by name only."""

from __future__ import annotations

import json
from typing import Annotated, Any, Self
from urllib.parse import quote

from pydantic import Field, StringConstraints, model_validator

from preflight.check import Section, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

Repo = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
Owner = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
SIGN_IN = "Could not query GitHub; check `gh auth status` and sign in if needed."


class GhError(Exception):
    pass


class GhNotFound(GhError):
    pass


def gh_api(ctx, path: str) -> Any:
    result = ctx.host.run("gh api %s", path)
    if result.rc == 0:
        text = result.stdout.strip()
        return json.loads(text) if text else None
    if "HTTP 404" in result.stderr:
        raise GhNotFound(path)
    raise GhError(f"gh api exited {result.rc}")


def _gh_error(key: str | None, exc: Exception) -> Item:
    return error(key, do=SIGN_IN, paste="gh auth login", error_type=type(exc).__name__)


def _all(items: list[Item]) -> Outcome:
    return outcome(*items) if items else outcome(ok(observed="nothing to check"))


class AuthSection(Section):
    hostname: str = "github.com"


@check("github.auth", section=AuthSection)
def auth(ctx, s: AuthSection) -> Outcome:
    result = ctx.host.run("gh auth status --hostname %s", s.hostname)
    if result.rc == 127:
        return outcome(error(do="Install the GitHub CLI (gh).", error_type="MissingTool"))
    if result.rc == 0:
        return outcome(ok())
    return outcome(fail(do=f"Sign in to {s.hostname} with the GitHub CLI.", paste="gh auth login"))


class RepoSection(Section):
    repo: Repo
    actions_access: str | None = None


@check("github.repo", section=RepoSection)
def repo(ctx, s: RepoSection) -> Outcome:
    try:
        gh_api(ctx, f"repos/{s.repo}")
    except GhNotFound:
        return outcome(
            fail(
                "exists",
                do=f"Create the repository {s.repo}, or correct its name in the contract.",
                paste=f"gh repo create {s.repo} --private",
            )
        )
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    if s.actions_access is not None:
        try:
            access = (gh_api(ctx, f"repos/{s.repo}/actions/permissions/access") or {}).get(
                "access_level"
            )
        except GhError as exc:
            items.append(_gh_error("actions_access", exc))
        else:
            if access == s.actions_access:
                items.append(ok("actions_access", observed=access))
            else:
                items.append(
                    fail(
                        "actions_access",
                        do=(
                            f"Set Actions access for {s.repo} to {s.actions_access} "
                            "(Settings → Actions → General → Access)."
                        ),
                        paste=(
                            f"gh api -X PUT repos/{s.repo}/actions/permissions/access "
                            f"-f access_level={s.actions_access}"
                        ),
                        observed=access,
                    )
                )
    return outcome(*items)


class VariablesSection(Section):
    repo: Repo | None = None
    org: Owner | None = None
    variables: dict[str, str]

    @model_validator(mode="after")
    def one_owner(self) -> Self:
        if (self.repo is None) == (self.org is None):
            raise ValueError("give exactly one of repo or org")
        return self


@check("github.variables", section=VariablesSection)
def variables(ctx, s: VariablesSection) -> Outcome:
    base, flag = (f"orgs/{s.org}", f"--org {s.org}") if s.org else (f"repos/{s.repo}", f"--repo {s.repo}")
    items = []
    for name, expected in s.variables.items():
        paste = f"gh variable set {name} {flag} --body '{expected}'"
        try:
            value = (gh_api(ctx, f"{base}/actions/variables/{name}") or {}).get("value")
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
                fail(name, do=f"Set the Actions variable {name} to {expected!r}.", paste=paste, observed=value)
            )
    return _all(items)


class EnvironmentsSection(Section):
    repo: Repo
    environments: list[str] = Field(min_length=1)


@check("github.environments", section=EnvironmentsSection)
def environments(ctx, s: EnvironmentsSection) -> Outcome:
    items = []
    for name in s.environments:
        try:
            gh_api(ctx, f"repos/{s.repo}/environments/{quote(name)}")
        except GhNotFound:
            items.append(
                fail(
                    name,
                    do=f"Create the GitHub environment {name} in {s.repo}.",
                    paste=f"gh api -X PUT repos/{s.repo}/environments/{quote(name)}",
                )
            )
        except GhError as exc:
            items.append(_gh_error(name, exc))
        else:
            items.append(ok(name))
    return outcome(*items)


class RulesetSection(Section):
    repo: Repo
    name: str
    required_checks: list[str] = []
    enforcement: str = "active"


@check("github.ruleset", section=RulesetSection)
def ruleset(ctx, s: RulesetSection) -> Outcome:
    where = f"Settings → Rules → Rulesets in {s.repo}"
    try:
        summaries = gh_api(ctx, f"repos/{s.repo}/rulesets") or []
        match = next((r for r in summaries if r.get("name") == s.name), None)
        if match is None:
            return outcome(fail("exists", do=f"Create the ruleset {s.name!r} ({where}).", generic=True))
        detail = gh_api(ctx, f"repos/{s.repo}/rulesets/{match['id']}") or {}
    except GhError as exc:
        return outcome(_gh_error("exists", exc))
    items = [ok("exists")]
    enforcement = detail.get("enforcement")
    if enforcement == s.enforcement:
        items.append(ok("enforcement", observed=enforcement))
    else:
        items.append(
            fail(
                "enforcement",
                do=f"Set ruleset {s.name!r} enforcement to {s.enforcement} ({where}).",
                observed=enforcement,
            )
        )
    if s.required_checks:
        contexts = {
            check_.get("context")
            for rule in detail.get("rules", [])
            if rule.get("type") == "required_status_checks"
            for check_ in (rule.get("parameters") or {}).get("required_status_checks", [])
            if check_.get("context")
        }
        missing = [c for c in s.required_checks if c not in contexts]
        if missing:
            items.append(
                fail(
                    "required_checks",
                    do=(
                        f"Add the required status check(s) {', '.join(missing)} to ruleset "
                        f"{s.name!r} ({where})."
                    ),
                    observed=sorted(contexts),
                )
            )
        else:
            items.append(ok("required_checks", observed=sorted(contexts)))
    return outcome(*items)


class SecretNamesSection(Section):
    repo: Repo
    environment: str | None = None
    names: list[str] = Field(min_length=1)


@check("github.secret_names", section=SecretNamesSection)
def secret_names(ctx, s: SecretNamesSection) -> Outcome:
    if s.environment:
        path = f"repos/{s.repo}/environments/{quote(s.environment)}/secrets"
        where, flag = f" on environment {s.environment}", f" --env {s.environment}"
    else:
        path, where, flag = f"repos/{s.repo}/actions/secrets", "", ""
    try:
        present = {x.get("name") for x in (gh_api(ctx, path) or {}).get("secrets", [])}
    except GhNotFound:
        present = set()
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    return outcome(
        *(
            ok(name)
            if name in present
            else fail(
                name,
                do=f"Set the secret {name}{where}.",
                paste=f"gh secret set {name}{flag} --repo {s.repo} < <file holding the value>",
            )
            for name in s.names
        )
    )


class WorkflowSection(Section):
    repo: Repo
    workflow: str
    branch: str | None = None
    event: str | None = None


@check("github.workflow_green", section=WorkflowSection)
def workflow_green(ctx, s: WorkflowSection) -> Outcome:
    query = "per_page=1&status=completed"
    if s.branch:
        query += f"&branch={quote(s.branch)}"
    if s.event:
        query += f"&event={quote(s.event)}"
    try:
        body = gh_api(ctx, f"repos/{s.repo}/actions/workflows/{quote(s.workflow)}/runs?{query}")
    except GhError as exc:
        return outcome(_gh_error(None, exc))
    runs = (body or {}).get("workflow_runs", [])
    if not runs:
        return outcome(fail(do=f"{s.workflow} has no completed run yet; trigger one.", generic=True))
    last = runs[0]
    if last.get("conclusion") == "success":
        return outcome(ok(observed=last.get("html_url")))
    return outcome(
        fail(
            do=(
                f"The last {s.workflow} run concluded {last.get('conclusion')}; open it, fix the "
                "cause, and rerun."
            ),
            paste=last.get("html_url"),
            observed=last.get("conclusion"),
            generic=True,
        )
    )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_github.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. Rule order in the fakes matters, because the first substring match wins: the more specific patterns (`rulesets/7`, `actions/permissions/access`) come first. Keep that order if you edit the tests.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/github.py tests/test_catalog_github.py
git commit -m "feat(catalog): github settings, secrets by name, workflow results"
```

---

### Task 23: Catalog — git, files, sops

**Files:**
- Create: `src/preflight/catalog/git.py`, `src/preflight/catalog/files.py`, `src/preflight/catalog/sops.py`, `tests/test_catalog_repo.py`

**Interfaces:**
- Consumes: `Context.host`, `Context.path` and `Context.root` (Task 10).
- Produces:
  - `git.UpToDateSection(remote="origin", branch="main")` and the check `git.up_to_date`;
  - `files.FilesSection(paths)` and the checks `files.present`, `files.absent`, `files.git_ignored` and `files.committed`;
  - `sops.SopsRuleSection(paths, min_recipients=1, config=".sops.yaml")` and the check `sops.rule`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_repo.py
from fakes import FakeHost, FakeResult, make_ctx

from preflight.catalog import files, git, sops
from preflight.outcome import Status

SHA = "a" * 40


def run(check, section, tmp_path, host):
    return check.observe(make_ctx(tmp_path, host=host), section)


def test_up_to_date(tmp_path):
    section = git.UpToDateSection()
    remote = ("ls-remote", FakeResult(0, f"{SHA}\trefs/heads/main\n"))
    ok_host = FakeHost([remote, ("cat-file", FakeResult(0)), ("merge-base", FakeResult(0))])
    assert run(git.up_to_date, section, tmp_path, ok_host).status is Status.OK
    assert "refs/heads/main" in ok_host.commands[0]
    behind = FakeHost([remote, ("cat-file", FakeResult(0)), ("merge-base", FakeResult(1))])
    item = run(git.up_to_date, section, tmp_path, behind).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "git fetch origin && git rebase origin/main")
    unfetched = FakeHost([remote, ("cat-file", FakeResult(128))])
    assert run(git.up_to_date, section, tmp_path, unfetched).status is Status.FAIL
    offline = FakeHost([("ls-remote", FakeResult(128))])
    assert run(git.up_to_date, section, tmp_path, offline).items[0].error_type == "GitRemote"


def test_files_present_and_absent(tmp_path):
    host = FakeHost(files={f"{tmp_path}/README.md": "x"})
    section = files.FilesSection(paths=["README.md", "secrets/dev.yaml"])
    present = {i.key: i.status for i in run(files.present, section, tmp_path, host).items}
    assert present == {"README.md": Status.OK, "secrets/dev.yaml": Status.FAIL}
    absent = {i.key: i.status for i in run(files.absent, section, tmp_path, host).items}
    assert absent == {"README.md": Status.FAIL, "secrets/dev.yaml": Status.OK}


def test_files_git_ignored_and_committed(tmp_path):
    section = files.FilesSection(paths=["secrets/prod.sops.yaml"])
    ignored = FakeHost([("check-ignore", FakeResult(1))])
    item = run(files.git_ignored, section, tmp_path, ignored).items[0]
    assert item.next_step.paste == "echo 'secrets/prod.sops.yaml' >> .gitignore"
    committed = FakeHost([("ls-files", FakeResult(0)), ("diff --quiet", FakeResult(1))])
    assert run(files.committed, section, tmp_path, committed).status is Status.FAIL


def test_sops_rule(tmp_path):
    config = (
        "creation_rules:\n"
        "  - path_regex: secrets/dev\\.(sops\\.)?yaml$\n"
        "    age: age1me,age1ci\n"
        "  - path_regex: secrets/prod\\.(sops\\.)?yaml$\n"
        "    age: age1me\n"
    )
    host = FakeHost(files={f"{tmp_path}/.sops.yaml": config})
    section = sops.SopsRuleSection(paths=["secrets/dev.sops.yaml", "secrets/prod.sops.yaml", "secrets/qa.yaml"], min_recipients=2)
    items = {i.key: i.status for i in run(sops.rule, section, tmp_path, host).items}
    assert items == {"secrets/dev.sops.yaml": Status.OK, "secrets/prod.sops.yaml": Status.FAIL, "secrets/qa.yaml": Status.FAIL}
    missing = run(sops.rule, section, tmp_path, FakeHost()).items[0]
    assert (missing.status, missing.next_step.generic) == (Status.FAIL, True)
    broken = FakeHost(files={f"{tmp_path}/.sops.yaml": "creation_rules:\n  - path_regex: '('\n"})
    assert run(sops.rule, section, tmp_path, broken).items[0].error_type == "RegexError"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_catalog_repo.py -v`
Expected: FAIL (`ImportError: cannot import name 'files'`).

- [ ] **Step 3: Implement**

```python
# src/preflight/catalog/git.py
"""The checkout contains the remote branch's latest commit. Reads the remote with ls-remote and
never fetches, so checking changes nothing."""

from __future__ import annotations

from preflight.check import Section, check
from preflight.outcome import Outcome, error, fail, ok, outcome


class UpToDateSection(Section):
    remote: str = "origin"
    branch: str = "main"


@check("git.up_to_date", section=UpToDateSection)
def up_to_date(ctx, s: UpToDateSection) -> Outcome:
    root = str(ctx.root)
    ref = f"{s.remote}/{s.branch}"
    paste = f"git fetch {s.remote} && git rebase {ref}"
    remote = ctx.host.run("git -C %s ls-remote %s %s", root, s.remote, f"refs/heads/{s.branch}")
    if remote.rc != 0 or not remote.stdout.strip():
        return outcome(
            error(
                do=f"Could not read {ref} from the remote; check your network and git credentials.",
                error_type="GitRemote",
            )
        )
    sha = remote.stdout.split()[0]
    if ctx.host.run("git -C %s cat-file -e %s", root, f"{sha}^{{commit}}").rc != 0:
        return outcome(
            fail(
                do=f"Your checkout does not have the latest {ref}; fetch it and rebase onto it.",
                paste=paste,
                observed=sha[:12],
            )
        )
    ancestor = ctx.host.run("git -C %s merge-base --is-ancestor %s HEAD", root, sha)
    if ancestor.rc == 0:
        return outcome(ok(observed=sha[:12]))
    if ancestor.rc == 1:
        return outcome(
            fail(
                do=f"Your branch does not contain the latest {ref}; rebase onto it first.",
                paste=paste,
                observed=sha[:12],
            )
        )
    return outcome(error(do="git could not compare your branch with the remote.", error_type="GitError"))
```

```python
# src/preflight/catalog/files.py
"""Files in the consumer's repository: present, absent, git-ignored, committed. Existence and
git status only; never the contents."""

from __future__ import annotations

from pydantic import Field

from preflight.check import Section, check
from preflight.outcome import Item, Outcome, error, fail, ok, outcome


class FilesSection(Section):
    paths: list[str] = Field(min_length=1)


def _exists(ctx, path: str) -> bool:
    return ctx.host.file(str(ctx.path(path))).exists


@check("files.present", section=FilesSection)
def present(ctx, s: FilesSection) -> Outcome:
    return outcome(
        *(ok(p) if _exists(ctx, p) else fail(p, do=f"Create {p}.", generic=True) for p in s.paths)
    )


@check("files.absent", section=FilesSection)
def absent(ctx, s: FilesSection) -> Outcome:
    return outcome(
        *(fail(p, do=f"Remove {p}.", generic=True) if _exists(ctx, p) else ok(p) for p in s.paths)
    )


def _ignored(ctx, path: str) -> Item:
    result = ctx.host.run("git -C %s check-ignore -q -- %s", str(ctx.root), path)
    if result.rc == 0:
        return ok(path)
    if result.rc == 1:
        return fail(path, do=f"Add {path} to .gitignore.", paste=f"echo '{path}' >> .gitignore")
    return error(path, do="git could not check .gitignore here.", error_type="GitError")


@check("files.git_ignored", section=FilesSection)
def git_ignored(ctx, s: FilesSection) -> Outcome:
    return outcome(*(_ignored(ctx, p) for p in s.paths))


def _committed(ctx, path: str) -> Item:
    root = str(ctx.root)
    tracked = ctx.host.run("git -C %s ls-files --error-unmatch -- %s", root, path).rc == 0
    clean = tracked and ctx.host.run("git -C %s diff --quiet HEAD -- %s", root, path).rc == 0
    if clean:
        return ok(path)
    return fail(
        path,
        do=f"Commit {path}.",
        paste=f"git add {path} && git commit -m 'chore: commit {path}'",
        generic=True,
    )


@check("files.committed", section=FilesSection)
def committed(ctx, s: FilesSection) -> Outcome:
    return outcome(*(_committed(ctx, p) for p in s.paths))
```

```python
# src/preflight/catalog/sops.py
"""A sops creation rule covers each secrets file, with enough age recipients. `.sops.yaml`
holds public keys only."""

from __future__ import annotations

import re

import yaml
from pydantic import Field

from preflight.check import Section, check
from preflight.outcome import Outcome, error, fail, ok, outcome


class SopsRuleSection(Section):
    paths: list[str] = Field(min_length=1)
    min_recipients: int = Field(default=1, ge=1)
    config: str = ".sops.yaml"


def _recipients(rule: dict) -> list[str]:
    age = rule.get("age", "")
    values = age if isinstance(age, list) else str(age).split(",")
    return [value.strip() for value in values if str(value).strip()]


@check("sops.rule", section=SopsRuleSection)
def rule(ctx, s: SopsRuleSection) -> Outcome:
    config = ctx.host.file(str(ctx.path(s.config)))
    if not config.exists:
        skeleton = "creation_rules:\n" + "\n".join(
            f"  - path_regex: {re.escape(p)}$\n    age: age1..." for p in s.paths
        )
        return outcome(
            fail(do=f"Create {s.config} with a creation rule for each secrets file.", paste=skeleton, generic=True)
        )
    try:
        rules = (yaml.safe_load(config.content_string) or {}).get("creation_rules") or []
    except yaml.YAMLError:
        return outcome(error(do=f"{s.config} is not valid YAML.", error_type="YAMLError"))
    rules = [r for r in rules if isinstance(r, dict)]
    items = []
    for path in s.paths:
        match = None
        for candidate in rules:
            try:
                if re.search(str(candidate.get("path_regex", "")), path):
                    match = candidate
                    break
            except re.error:
                return outcome(
                    error(
                        do=f"{s.config} has an invalid path_regex {candidate.get('path_regex')!r}.",
                        error_type="RegexError",
                    )
                )
        if match is None:
            items.append(fail(path, do=f"Add a creation rule to {s.config} whose path_regex matches {path}."))
            continue
        count = len(_recipients(match))
        if count < s.min_recipients:
            items.append(
                fail(
                    path,
                    do=f"The rule for {path} names {count} age recipient(s); it needs at least {s.min_recipients}.",
                    observed=count,
                )
            )
        else:
            items.append(ok(path, observed=count))
    return outcome(*items)
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_catalog_repo.py -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. Wrap any line ruff reports as too long.

- [ ] **Step 5: Commit**

```bash
git add src/preflight/catalog/git.py src/preflight/catalog/files.py src/preflight/catalog/sops.py tests/test_catalog_repo.py
git commit -m "feat(catalog): git.up_to_date, files.* and sops.rule"
```

---
### Task 24: End to end through the CLI

**Files:**
- Create: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: the whole package. `python -m preflight` (Task 17), with real worker processes and a real testinfra local host.

- [ ] **Step 1: Write the test**

```python
# tests/test_end_to_end.py
"""The CLI against a consumer repository: real worker processes, real testinfra, no network."""

import json
import subprocess
import sys

from conftest import write

LOCAL = """
from preflight import Section, check, ok, outcome


class Marker(Section):
    value: str


@check("e2e.marker", section=Marker)
def marker(ctx, s):
    return outcome(ok(observed=s.value))
"""

GATE = """
from preflight import Gate
from preflight.catalog import files
from consumer.checks.local import marker

gate = Gate("ready", checks=[files.present("required_files"), marker("marker")])
"""

CONTRACT = """
schema_version = 1
environment = "dev"

[required_files]
paths = ["README.md"]

[marker]
value = { tfvars = "envs/dev.tfvars", key = "marker", placeholder = ["CHANGEME"], how = "put the marker in envs/dev.tfvars" }
"""


def preflight(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "preflight", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
    )


def setup(repo, marker):
    write(repo, "preflight/checks/local.py", LOCAL)
    write(repo, "preflight/gates/ready.py", GATE)
    write(repo, "preflight/contracts/dev.toml", CONTRACT)
    write(repo, "envs/dev.tfvars", f'marker = "{marker}"\n')


CHECK = ("check", "preflight/gates/ready.py", "--contract", "preflight/contracts/dev.toml")


def test_an_unfinished_repository_names_each_step(repo):
    setup(repo, "CHANGEME")
    result = preflight(repo, *CHECK, "--json", "report.json")
    assert result.returncode == 1, result.stderr
    report = json.loads((repo / "report.json").read_text())
    assert report["open"] == [
        "files.present[required_files]:README.md",
        "contract.placeholder[marker.value]",
    ]
    statuses = {i["id"]: i["status"] for run in report["runs"] for i in run["instances"]}
    assert statuses["e2e.marker[marker]"] == "blocked"
    assert "> NEXT  files.present[required_files]:README.md" in result.stdout


def test_a_finished_repository_passes_check_status_and_validate(repo):
    setup(repo, "m")
    write(repo, "README.md", "hello\n")
    assert preflight(repo, *CHECK).returncode == 0
    status = preflight(repo, "status")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "  satisfied  ready" in status.stdout
    assert preflight(repo, "validate").returncode == 0
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_end_to_end.py -v`
Expected: PASS. If it fails, the failure is a real integration defect in the tasks before this one, such as consumer registration inside workers, `PATH`, or the JSON shape. Fix it in the owning module, with a unit test there, rather than weakening this test.

- [ ] **Step 3: Run the whole suite and lint**

Run: `uv run pytest -v && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_end_to_end.py
git commit -m "test: end to end through the CLI with a consumer repository"
```

---

### Task 25: README

**Files:**
- Replace: `README.md`

**Interfaces:** none. The README documents what Tasks 2–23 built, for a consumer and for someone maintaining preflight.

- [ ] **Step 1: Write `README.md`**

````markdown
# preflight

Guardrails for the steps only the operator can do. A script calls preflight first and stops
unless it exits 0; when something is not done, preflight names the next step, with text to paste.

```bash
# top of scripts/bootstrap.sh in a consumer repository
preflight() { uvx --from "git+https://github.com/strider4560/preflight@v0.1.0" preflight "$@"; }
preflight check preflight/gates/bootstrap_entry.py --contract "preflight/contracts/$env.toml" || exit 1
```

Preflight never runs the command it gates and never changes what it observes. It checks what
the operator is responsible for (DNS records in another account, secrets, IAM and GitHub
settings, sessions) and leaves what OpenTofu and the pipeline prove to them.

## Where was I?

```bash
uvx --from "git+https://github.com/strider4560/preflight@v0.1.0" preflight status
```

`status` runs every milestone gate against every contract and prints, per environment, which
gates are satisfied, which are open, and which wait on an earlier gate, then one overall NEXT.
Steps in gates that are still waiting are listed as "also open", because some expire (an SNS
confirmation link, a certificate's 72 hours).

## A consumer repository

```
preflight/
  contracts/dev.toml     # expected values for one environment
  contracts/prod.toml
  contracts/repo.toml    # optional: scope = "repository", for repository-wide gates
  gates/<name>.py        # one Gate each, named after its file
  checks/<name>.py       # checks only this repository needs
```

No Python project is needed: `uvx` provides preflight, and preflight imports the gate and
check files as the package `consumer` (`from consumer.checks.secrets import route`).

### Contracts

```toml
schema_version = 1
environment = "dev"

[identities.admin]
profile        = "sandbox"
region         = "us-east-1"
account_id     = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID" }
permission_set = "AWSAdministratorAccess"

[delegation]
root         = { tfvars = "envs/dev.tfvars", key = "root_domain" }
zones        = { tfvars = "envs/dev.tfvars", key = "zones" }
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json", how = "rerun scripts/bootstrap.sh dev from an up-to-date main" }

[delegation.remedy]
ref = "README, DNS step 1"
```

- A value is a literal or a reference: `tfvars` (`key`), `yaml` / `json` (`path`),
  `yaml_glob` (`path`, a list of `{file, value}`), or `ssm` (`identity`, `format`, `path`),
  which is read when the check runs. References resolve from the repository root.
- `placeholder` lists values meaning "not filled in yet"; a placeholder becomes a failing step
  ("fill `account_id` in `envs/dev.tfvars`") that blocks everything using it. `how` says where
  the value comes from.
- `[<section>.remedy]` (`do`, `paste`, `wait`, `ref`, with `{environment}` and the section's
  scalar fields) adds repository-specific guidance to a generic check.
- Every section accepts `region` and `timeout`.

### Gates

```python
from preflight import Gate
from preflight.catalog import aws, git

gate = Gate(
    "bootstrap_entry",
    guards="scripts/bootstrap.sh",   # an entry gate: left out of `status`
    requires=["identifiers"],        # those gates' checks run first, as prerequisites
    checks=[aws.assumed("admin"), aws.region("admin"), git.up_to_date("branch")],
)
```

A check bound to a section is `check("section")`; an AWS identity check is `aws.session("admin")`.

### Repository-local checks

```python
from preflight import Section, check, fail, ok, outcome, session_for


class Route(Section):
    identity: str
    committed: bool


@check("iac.secrets_route", section=Route, requires=[session_for("identity")])
def route(ctx, s):
    if s.committed:
        return outcome(ok())
    return outcome(fail(do="Commit secrets/dev.sops.yaml.", paste="git add secrets/dev.sops.yaml"))
```

A check observes through `ctx.host` (testinfra: `run`, `file`), `ctx.ansible_host` and
`ctx.aws_module(...)` (Ansible modules with the identity's profile and region),
`ctx.ssm_lookup(name)`, and `ctx.dns`. It returns an `Outcome` of items: `ok`, `fail`
(the operator has something to do), `pending` (done, settling), `error` (could not observe).
Items may be `advisory`. Never put secret values in an item.

## Catalog

| Check | What it proves |
|---|---|
| `aws.session[<identity>]` | Preflight can observe as the identity (explicit profile) |
| `aws.assumed[<identity>]` | The caller's own shell acts as the identity |
| `aws.region[<identity>]` | The profile's configured region |
| `ssm.present`, `ssm.parameters` | Parameters exist and are non-empty (read without decryption) |
| `acm.issued` | Issued; or the exact validation CNAME to add; or waiting |
| `tofu.plan_clean` | `tofu plan -detailed-exitcode` is 0 (no lock, read-only) |
| `dns.delegated`, `dns.undelegated` | The parent zone's own servers delegate exactly the expected servers, or nothing |
| `dns.cname`, `dns.caa` | Records the operator adds by hand |
| `github.auth`, `repo`, `variables`, `environments`, `ruleset`, `secret_names`, `workflow_green` | GitHub settings; secrets by name only |
| `git.up_to_date` | The checkout contains the remote branch's latest commit (no fetch) |
| `files.present`, `absent`, `git_ignored`, `committed` | Files and their git status |
| `sops.rule` | A creation rule with enough age recipients covers each secrets file |

## Commands and exit codes

| Command | Exit |
|---|---|
| `preflight check <gate.py> --contract <file> [--json F] [--junit F] [--jobs N]` | 0 every blocking item ok; 1 not; 2 invalid contract or gate (nothing observed); 3 preflight bug; 130 interrupted |
| `preflight status [--gates D] [--contracts D] [--json F]` | 0 only when every milestone gate is satisfied everywhere |
| `preflight validate [--gates D] [--contracts D]` | 0 or 2; needs no credentials; add it to CI |

Each instance runs in its own worker process with a cleaned environment: stray AWS credential
variables and `TF_CLI_ARGS*`, `TF_WORKSPACE`, `TF_VAR_*` are removed, and the identity's
profile and region are set. Timeouts kill the worker's whole process group. JSON reports are
written with mode 0600; JUnit never marks anything skipped.

## Developing preflight

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

The design is `docs/superpowers/specs/2026-09-29-preflight-library-design.md`.
````

- [ ] **Step 2: Check the README against the code**

Run: `uv run python -c "import preflight.catalog.aws, preflight.catalog.git; from preflight import Gate, Section, check, fail, ok, outcome, session_for"`
Expected: no error, so every name the README imports exists.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README for the library, its consumers and its catalog"
```

---

### Task 26: Live acceptance against Sandbox (operator present)

This task needs the operator's credentials and is run with the operator. Nothing here is committed except the results section of this plan.

**Files:**
- Create (scratch, outside every repository): `$ACCEPT/preflight/contracts/dev.toml`, `$ACCEPT/preflight/gates/acceptance.py`
- Modify: this plan, section "Acceptance results"

- [ ] **Step 1: Build a scratch consumer**

```bash
ACCEPT="$(mktemp -d)" && git -C "$ACCEPT" init -q && mkdir -p "$ACCEPT/preflight/gates" "$ACCEPT/preflight/contracts"
grep expected_account_id contracts/sandbox.local.toml   # the Sandbox account ID to use below
```

Write `$ACCEPT/preflight/contracts/dev.toml`, replacing `<SANDBOX_ACCOUNT_ID>`:

```toml
schema_version = 1
environment = "dev"

[identities.admin]
profile = "sandbox"
region = "us-east-1"
account_id = "<SANDBOX_ACCOUNT_ID>"
permission_set = "AWSAdministratorAccess"

[bootstrap_params]
identity = "admin"
names = ["/platform/state/bucket", "/platform/state/kms_key_arn", "/platform/oidc/provider_arn"]

[bootstrap_stack]
identity = "admin"
dir = "/home/dwiley/Develop/tellabs/iac/stacks/bootstrap"
var_files = [
  "/home/dwiley/Develop/tellabs/iac/envs/dev.tfvars",
  "/home/dwiley/Develop/tellabs/iac/stacks/bootstrap/envs/dev.tfvars",
  "/home/dwiley/Develop/tellabs/iac/stacks/bootstrap/envs/common.tfvars",
]

[acceptance_zone]
root = "tellabs.dev"
zones = ["preflight-acceptance"]
name_servers = { preflight-acceptance = ["ns-1.example.com", "ns-2.example.com"] }

[root_caa]
domain = "tellabs.dev"
issuers = ["amazon.com"]

[repository]
repo = "tellabsadmin/iac"

[platform_run]
repo = "tellabsadmin/iac"
workflow = "platform.yml"
branch = "main"
```

Write `$ACCEPT/preflight/gates/acceptance.py`:

```python
from preflight import Gate
from preflight.catalog import aws, dns, github, ssm, tofu

gate = Gate(
    "acceptance",
    checks=[
        aws.session("admin"),
        aws.assumed("admin"),
        aws.region("admin"),
        ssm.parameters("bootstrap_params"),
        tofu.plan_clean("bootstrap_stack"),
        dns.delegated("acceptance_zone"),
        dns.caa("root_caa"),
        github.auth("repository"),
        github.repo("repository"),
        github.workflow_green("platform_run"),
    ],
)
```

- [ ] **Step 2: Run it logged out, then logged in**

```bash
cd "$ACCEPT"
PF="uv run --project /home/dwiley/Develop/preflight preflight"
unset AWS_PROFILE; $PF check preflight/gates/acceptance.py --contract preflight/contracts/dev.toml
aws sso login --profile sandbox && export AWS_PROFILE=sandbox
$PF check preflight/gates/acceptance.py --contract preflight/contracts/dev.toml --json "$ACCEPT/report.json"
```

Expected:
- **Logged out:**
  - `aws.session[admin]` is an `error` pasting `aws sso login --profile sandbox`;
  - the SSM and tofu instances are `blocked`;
  - `aws.assumed[admin]` names `export AWS_PROFILE=sandbox`.
- **Logged in:** every check is either `ok` or a `fail`/`pending` whose next step is right for the account's actual state. In particular:
  - `dns.delegated[acceptance_zone]:preflight-acceptance` fails with exactly the two-line NS block for `preflight-acceptance.tellabs.dev` (spec Acceptance 4);
  - `tofu.plan_clean` leaves no `.tflock` object and no change under `iac/stacks/bootstrap` (`git -C ~/Develop/tellabs/iac status --short stacks/bootstrap` prints nothing new).

- [ ] **Step 3: `acm.issued` when a pending certificate exists**

Look for a certificate in `PENDING_VALIDATION` in Sandbox: `aws acm list-certificates --certificate-statuses PENDING_VALIDATION --profile sandbox`. If one exists, add a `[pending_cert]` section (`identity = "admin"`, `certificate_arn = "<arn>"`) and `acm.issued("pending_cert")` to the gate, then rerun Step 2. It must report `fail` with the validation CNAME while the record is absent, independently of any delegation. If none exists, record that Task 19's unit tests are the evidence.

- [ ] **Step 4: Record the results and clean up**

Fill "Acceptance results" below: each instance's status, and anything whose next step was wrong or unclear (each one is a follow-up fix with its own test). Then `rm -rf "$ACCEPT"` and commit:

```bash
git add docs/superpowers/plans/2026-09-29-preflight-library.md
git commit -m "docs(plan): record live acceptance results"
```

---

## Spike results

Recorded by Task 1 on 2026-09-29. Environment: `uv venv` plus `ansible>=11`, `pytest-testinfra>=10,<11`, `boto3>=1.40,<2`, `python-hcl2>=8,<9` resolved to `ansible` 14.4.0, `ansible-core` 2.21.4, `amazon.aws` 11.4.0, `community.aws` 11.1.0, `boto3`/`botocore` 1.43.105, `python-hcl2` 8.1.4. `lookup/ssm_parameter.py` (amazon.aws) and `modules/acm_certificate_info.py` (community.aws) are both present. An SSO session for `sandbox` was active, so Questions 1 and 2 are live answers (read-only calls only).

- **Question 1 (testinfra Ansible backend runs AWS info modules): yes.** Logged in: `amazon.aws.aws_caller_info` returned keys `['account', 'account_alias', 'arn', 'changed', 'user_id']`; `community.aws.acm_certificate_info` returned `['certificates', 'changed']`. With `SPIKE_PROFILE=nonexistent-profile` neither raised: both returned `['boto3_version', 'botocore_version', 'changed', 'msg']` with `msg` = "Couldn't connect to AWS: The config profile (nonexistent-profile) could not be found", the same with `check=True` and `check=False`. **Consequence for Tasks 10-12 and 19:** a module failure does not raise in this environment. `Context.aws_module` must treat a result as failed when it lacks the keys the probe needs (or has no `account`/`certificates`) and surface `msg`; it must not rely on `AnsibleException`.
- **Question 2 (SSM lookup through `ansible.builtin.debug`): yes.** Existing parameter `/platform/state/bucket`: result keys `['changed', 'msg']`, `msg` = `<non-empty value returned>`. Missing parameter `/preflight/spike/does-not-exist` with `on_missing='skip'`: result keys `['changed']` only, so `result.get("msg")` is `None` (the `msg` key is absent). This is the exact "missing" rendering; the tuple `None`, `""`, `"None"` in Task 10's `Context.ssm_lookup` is sufficient. Failure shape (bad profile): `msg` is a string beginning `Task failed: Finalization of task args for 'ansible.builtin.debug' failed:` and containing `The lookup plugin 'amazon.aws.ssm_parameter' failed: Couldn't connect to AWS: ...`, with no exception raised. **Consequence:** `ssm_lookup` must treat a `msg` starting with `Task failed:` as an error (not as a value), or a credential failure reads as a present parameter.
- **Question 3 (killing a process group ends an `ansible` grandchild): yes.** `group before: 2 after: 0` with `start_new_session=True` and `os.killpg(..., SIGKILL)`.
- **Question 4 (python-hcl2 `strip_string_quotes=True, preserve_heredocs=False, with_comments=False`): yes, plain values, unquoted.** Confirmed on python-hcl2 8.1.4 with a synthetic tfvars (`{'env': 'dev', 'account_id': '123456789012', 'enabled': True, 'count': 3, 'tags': {'team': 'platform'}, 'zones': ['a', 'b']}`); the real `iac/envs/dev.tfvars` was not read in the spike (blocked by the session's data-handling policy), so it stays confirmed only by the planning-time observation.
- **Fallback decisions:** none. No probe falls back to `ctx.host.run` of the `aws` CLI. Run scripts with `</dev/null` and redirect output to a file if ansible reports non-blocking stdio.

## Acceptance results

Task 26 fills this in: each instance's status against Sandbox, and any follow-up fixes.
