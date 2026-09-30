# Scoped runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a gate program act after its guards pass and verify afterwards (`Gate.checked()`), add the S3 observations iac's bootstrap decides on, and rewrite `iac/scripts/bootstrap.sh` as `scripts/bootstrap.py` on that API.

**Architecture:** `Gate`'s run loop moves into a `_Session` that `execute()` opens and closes around the guards, and that `checked()` keeps open for the block; a `Run` exposes the parsed arguments, resolved providers and `verify()`. Two new catalog checks observe S3 facts; `observed()` reads a check's observed value inside a provider. iac's program keeps the shell's tofu steps one for one, inside the block.

**Tech Stack:** Python ≥3.12, pydantic 2, testinfra/Ansible (`amazon.aws.s3_object` in list mode), the `aws` CLI for `head-bucket`, `uv`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-29-scoped-runs-design.md` (extends `2026-09-29-preflight-gate-programs-design.md`).

## Global Constraints

- ruff `line-length = 100`, `select = ["E", "F", "I", "B", "UP"]`; done means `uv run pytest -q` and `uv run ruff check src tests && uv run ruff format --check src tests` pass. Split a long message literal into adjacent literals rather than rewording it.
- `run()`'s behavior and exit codes are unchanged: 0 passed; 1 a guard stopped the run; 2 the gate (or, in a block, the program) is wrong; 3 preflight failed; 130 interrupted. Exception messages never reach output; types only.
- Preflight never runs the command it gates: the library gains no tofu, apply or subprocess-running feature; the consumer's program runs its own actions inside the block.
- Providers stay alive for the block and clean up when it ends, however it ends.
- Commits: Conventional Commits with scope, ending with
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
  ```
- Preflight work on branch `feat/scoped-runs`; iac work on a new branch off `main` in `~/Develop/tellabs/iac-preflight-bootstrap` (currently on `chore/preflight-v0.1.0`; branch from `origin/main` once #10 is merged, else from that branch). Never push, tag or open a pull request without the operator's approval.
- Subagents on Opus 5.5 or Sonnet 5.5 only, always explicit.

## Review Focus

1. A program whose block raises `SystemExit(0)` (its own early success) must still run cleanup and exit 0; one that raises any other exception must exit 2 with the type only. Test: Task 1, `test_a_system_exit_from_the_block_keeps_its_code`, `test_an_exception_in_the_block_is_the_programs_failure`.
2. Ctrl-C while the block is running a long subprocess: workers killed, cleanup run, exit 130. Test: Task 1, `test_an_interrupt_in_the_block_cleans_up_and_exits_130`.
3. `run[provider]` for a provider no guard used: resolved now, cached, and an `Unmet` there exits 1 with the steps shown. Test: Task 1, `test_run_resolves_providers_on_demand_and_reports_unmet`.
4. `s3.bucket_status` on a bucket owned by another account must observe `"forbidden"` and never pass as absent (the shell's 403 rule). Test: Task 2, `test_bucket_status`.
5. The iac program must never reach `tofu apply` when a guard stopped, and must restore `backend.tf` if the apply fails midway. Task 3 acceptance and `test_backend_is_restored_after_a_failed_apply` (a unit test of the program's helper with `tofu` replaced by a failing stub).

---

## File Structure

| Path | Change |
|---|---|
| `src/preflight/gate.py` | `_Prepared`, `_Session`, `Run`, `Gate.checked`, `_prepare`, `_emit`, `_progress`, `_unique_guards`; `_execute`/`_run` refactored onto `_Session` |
| `src/preflight/params.py` | `observed(outcome, key=None)` |
| `src/preflight/catalog/s3.py` | `bucket_status`, `object_exists` |
| `src/preflight/__init__.py` | exports `Run`, `observed`; version `0.2.0` |
| `README.md` | "A program that acts after its guards"; `s3` catalog rows |
| `tests/test_gate.py`, `tests/test_params.py`, `tests/test_catalog_s3.py`, `tests/test_end_to_end.py` | tests |
| iac `scripts/bootstrap.py` (new), `preflight/iac.py`, `preflight/bootstrap.py`, `Taskfile.yml`, `.github/workflows/platform.yml`, `README.md`, `AGENTS.md`; delete `scripts/bootstrap.sh`, `preflight/bootstrap_entry.py` | the program |

---

### Task 1: `Gate.checked()`, `Run` and `observed()`

**Files:**
- Modify: `src/preflight/gate.py`, `src/preflight/params.py`, `src/preflight/__init__.py`
- Test: `tests/test_gate.py` (append), `tests/test_params.py` (append), `tests/test_end_to_end.py` (append)

**Interfaces:**
- Consumes: everything in `gate.py` today (`Gate`, `Guards`, `Guard`, `_guard`, `_labels`, `_finish`, `_terminate_as_interrupt`), `Resolver`, `Unmet`, `ProviderFailed`, `Propagate`, `using`, `kill_all`, `reset_stop`, `RunResult`, `GuardResult`, `worklist`.
- Produces:
  - `Gate.checked(argv: Sequence[str] | None = None, *, executor: Executor | None = None, root: Path | None = None) -> ContextManager[Run]`.
  - `Run` with `.args: Mapping[str, Any]`, `__getitem__(provider) -> Any`, `verify(guards: Guards) -> None`.
  - `preflight.params.observed(outcome: Outcome, key: str | None = None) -> Any`.
  - `from preflight import Run, observed`.

- [ ] **Step 1: Write the failing gate tests**

Append to `tests/test_gate.py`:

```python
def checked_gate(log):
    def session() -> Iterator[str]:
        log.append("enter")
        yield "s"
        log.append("exit")

    def lazy() -> str:
        log.append("lazy")
        return "L"

    gate = Gate("program")

    @gate.guard("ready")
    def ready(env: Env, s: Annotated[str, Depends(session)]):
        return [thing(name=env)]

    return gate, session, lazy


def test_checked_enters_the_block_with_arguments_and_providers(tmp_path, capsys):
    log = []
    gate, session, lazy = checked_gate(log)
    with gate.checked(["dev"], executor=Scripted(), root=tmp_path) as run:
        assert run.args["env"] == "dev"
        assert run[session] == "s"
        assert log == ["enter"]
        assert run[lazy] == "L" and run[lazy] == "L"
        assert log == ["enter", "lazy"]
        log.append("block")
    assert log == ["enter", "lazy", "block", "exit"]
    assert "  ✓ ready\n" in capsys.readouterr().out


def test_checked_exits_1_before_the_block_when_a_guard_stops(tmp_path, capsys):
    log = []
    gate, _, _ = checked_gate(log)
    stop = Scripted({"test_gate.thing(dev)": outcome(fail(do="Not yet."))})
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev"], executor=stop, root=tmp_path):
            log.append("block")
    assert exc.value.code == 1
    assert log == ["enter", "exit"]
    assert "Not yet." in capsys.readouterr().out


def test_checked_under_validate_exits_0_without_the_block(tmp_path, capsys):
    log = []
    gate, _, _ = checked_gate(log)
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev", "--validate"], executor=Scripted(), root=tmp_path):
            log.append("block")
    assert exc.value.code == 0
    assert "block" not in log
    assert "Validated every guard" in capsys.readouterr().out


def test_verify_runs_more_guards_in_the_same_scope(tmp_path, capsys):
    log = []
    gate, session, _ = checked_gate(log)
    after = Guards()

    @after.guard("published")
    def published(s: Annotated[str, Depends(session)]):
        return [thing(name=f"after-{s}")]

    executor = Scripted()
    with gate.checked(["dev"], executor=executor, root=tmp_path) as run:
        run.verify(after)
    assert executor.ran == ["test_gate.thing(dev)", "test_gate.thing(after-s)"]
    assert log == ["enter", "exit"]
    assert capsys.readouterr().out.count("Every guard passed.") == 2


def test_verify_that_stops_exits_1_and_still_cleans_up(tmp_path, capsys):
    log = []
    gate, _, _ = checked_gate(log)
    after = Guards()
    after.guard("published")(lambda: [thing(name="x")])
    stop = Scripted({"test_gate.thing(x)": outcome(fail(do="Publish it."))})
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev"], executor=stop, root=tmp_path) as run:
            run.verify(after)
            log.append("unreachable")
    assert exc.value.code == 1
    assert log == ["enter", "exit"]
    assert "Publish it." in capsys.readouterr().out


def test_an_exception_in_the_block_is_the_programs_failure(tmp_path, capsys):
    log = []
    gate, _, _ = checked_gate(log)
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev"], executor=Scripted(), root=tmp_path):
            raise KeyError("account_id=123456789012")
    assert exc.value.code == 2
    assert log == ["enter", "exit"]
    err = capsys.readouterr().err
    assert "the program raised KeyError" in err and "123456789012" not in err


def test_a_system_exit_from_the_block_keeps_its_code(tmp_path):
    log = []
    gate, _, _ = checked_gate(log)
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev"], executor=Scripted(), root=tmp_path):
            raise SystemExit(0)
    assert exc.value.code == 0
    assert log == ["enter", "exit"]


def test_an_interrupt_in_the_block_cleans_up_and_exits_130(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(gate_module, "kill_all", lambda: killed.append(True))
    log = []
    gate, _, _ = checked_gate(log)
    with pytest.raises(SystemExit) as exc:
        with gate.checked(["dev"], executor=Scripted(), root=tmp_path):
            raise KeyboardInterrupt
    assert exc.value.code == 130
    assert killed == [True] and log == ["enter", "exit"]


def test_run_resolves_providers_on_demand_and_reports_unmet(tmp_path, capsys):
    def signed() -> str:
        raise Unmet(fail(do="Sign in to profile sandbox.", paste="aws sso login"))

    gate = Gate("program")
    gate.guard("ready")(lambda: [thing(name="r")])
    with pytest.raises(SystemExit) as exc:
        with gate.checked([], executor=Scripted(), root=tmp_path) as run:
            run[signed]
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "needs signed" in out and "aws sso login" in out


def test_a_cleanup_failure_after_a_passing_block_exits_3(tmp_path, capsys):
    def session() -> Iterator[str]:
        yield "s"
        raise RuntimeError("secret")

    gate = Gate("program")

    @gate.guard("ready")
    def ready(s: Annotated[str, Depends(session)]):
        return [thing(name=s)]

    with pytest.raises(SystemExit) as exc:
        with gate.checked([], executor=Scripted(), root=tmp_path):
            pass
    assert exc.value.code == 3
    assert "provider session cleanup raised RuntimeError" in capsys.readouterr().err
```

Append to `tests/test_params.py`:

```python
from preflight.params import observed  # noqa: E402  (add to the top import block instead)


def test_observed_reads_an_ok_items_value_or_raises_unmet():
    from preflight.outcome import ok

    assert observed(outcome(ok(observed="present"))) == "present"
    assert observed(outcome(ok("a", observed=1), ok("b", observed=2)), key="b") == 2
    with pytest.raises(Unmet) as exc:
        observed(outcome(fail(do="Could not look.")))
    assert exc.value.items[0].next_step.do == "Could not look."
    with pytest.raises(KeyError):
        observed(outcome(ok("a")), key="zzz")
```

(Put the `observed` import in the file's top import block and the `ok` import there too; no mid-file imports.)

Append to `tests/test_end_to_end.py`:

```python
PROGRAM = '''
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from preflight import Arg, Depends, Gate, Guards, Outcome, Probe, check, fail, ok, outcome
from preflight.catalog import files

Env = Annotated[Literal["dev", "prod"], Arg()]
ROOT = Path(__file__).resolve().parent.parent


def session(env: Env) -> Iterator[str]:
    yield env
    (ROOT / "cleanup.log").write_text(env)


gate = Gate("program")


@gate.guard("readme present")
def readme():
    return [files.present(paths=["README.md"])]


after = Guards()


@after.guard("marker written")
def marker():
    return [files.present(paths=["MARKER"])]


if __name__ == "__main__":
    with gate.checked(sys.argv[1:]) as run:
        (ROOT / "MARKER").write_text(run[session])
        run.verify(after)
'''


def test_a_program_acts_after_its_guards_and_verifies(repo):
    program = write(repo, "gate/program.py", PROGRAM)
    write(repo, "README.md", "x\n")
    result = run(program, "dev", cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "MARKER").read_text() == "dev"
    assert (repo / "cleanup.log").read_text() == "dev"
    assert result.stdout.count("Every guard passed.") == 2


def test_a_program_never_acts_when_a_guard_stops(repo):
    program = write(repo, "gate/program.py", PROGRAM)
    result = run(program, "dev", cwd=repo)
    assert result.returncode == 1
    assert not (repo / "MARKER").exists()
    assert "Create README.md." in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gate.py tests/test_params.py tests/test_end_to_end.py -q`
Expected: failures (`Gate` has no attribute `checked`; `observed` cannot be imported).

- [ ] **Step 3: Add `observed` to `src/preflight/params.py`**

Append (and add `Status` to the `preflight.outcome` import):

```python
def observed(outcome: Outcome, key: str | None = None) -> Any:
    """The observed value of the ok item with `key`, for a provider deciding on a fact; an item
    that is not ok raises Unmet carrying the whole outcome, so the guard shows its next step."""
    for item in outcome.items:
        if item.key == key:
            if item.status is not Status.OK:
                raise Unmet(outcome)
            return item.observed
    raise KeyError(key)
```

- [ ] **Step 4: Refactor `src/preflight/gate.py`**

Add to the imports: `from types import MappingProxyType`, `from collections.abc import Mapping`, and `from preflight.params import ... Unmet ...` already present. Then:

1. Replace `Gate.run` with:

```python
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
        with _terminate_as_interrupt():
            try:
                prepared = self._prepare(base, executor, root)
            except KeyboardInterrupt:
                raise SystemExit(130) from None
            if isinstance(prepared, RunResult):
                _emit(prepared)
                raise SystemExit(prepared.exit_code)
            session = _Session(self, prepared.values, prepared.executor)
            session.open()
            code = 0
            try:
                result = _finish(session.run_guards(prepared.base, prepared.guards, _progress))
                _emit(result)
                if result.exit_code != 0 or result.validated:
                    raise SystemExit(result.exit_code)
                yield Run(self, session, prepared)
            except KeyboardInterrupt:
                kill_all()
                code = 130
            except (SystemExit, Propagate):
                raise
            except Exception as exc:
                kill_all()
                code = 2
                print(f"{self._prog()}: the program raised {type(exc).__name__}", file=sys.stderr)
            finally:
                session.close()
                for problem in session.resolver.cleanup_problems:
                    print(f"{self._prog()}: {problem}", file=sys.stderr)
            if session.resolver.cleanup_problems and code == 0:
                code = 3
            if code:
                raise SystemExit(code)
```

2. Replace `_execute` and `_run` with:

```python
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
```

3. Replace `_guards` with a call to a module-level helper, and add the new classes and helpers (before `_terminate_as_interrupt`):

```python
    def _guards(self) -> list[Guard]:
        return _unique_guards(self, self.name)


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
        self.gate = gate
        self._session = session
        self._prepared = prepared
        self.args: Mapping[str, Any] = MappingProxyType(dict(prepared.values))

    def __getitem__(self, provider: Callable[..., Any]) -> Any:
        try:
            return self._session.resolver.provide(provider)
        except Unmet as exc:
            name = exc.provider or getattr(provider, "__name__", "provider")
            guard = GuardResult(f"needs {name}", unmet=exc.items, unmet_by=name)
            _emit(_finish(replace(self._prepared.base, exit_code=1, guards=(guard,))))
            raise SystemExit(1) from None
        except ProviderFailed as exc:
            print(f"{self.gate._prog()}: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    def verify(self, guards: Guards) -> None:
        """Runs more guards in this scope; exits when one stops, returns when every one passed."""
        flat = _unique_guards(guards, self.gate.name)
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
```

`verify` with a `GateDefinitionError` (no guards, duplicate names) propagates as the program's exception (exit 2 with the type); that is acceptable.

- [ ] **Step 5: Export and run**

In `src/preflight/__init__.py`, import `Run` from `preflight.gate` and `observed` from `preflight.params`, add both to `__all__`.

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass (run `uv run ruff format src tests` first if needed).

- [ ] **Step 6: README**

In `README.md`, after the "A gate" section's bullets, add:

````markdown
## A program that acts after its guards

A gate that is also the action, such as an account bootstrap, uses a scoped run:

```python
if __name__ == "__main__":
    with gate.checked(sys.argv[1:]) as run:
        env, plan = run.args["env"], run[state_plan]
        apply(env, plan)                  # the program's own work; preflight never runs it
        run.verify(published)             # post-condition guards
```

`checked` runs the guards and enters the block only when every one passed (otherwise it prints
the worklist and exits as `run()` would; under `--validate` it exits 0 without entering).
Inside, the providers are still alive: `run.args` holds the parsed arguments, `run[provider]`
a provider's value, and `run.verify(guards)` runs more guards, exiting 1 if one stops. Leaving
the block runs the providers' cleanup however it ends: a `SystemExit` keeps its code, Ctrl-C or
SIGTERM exits 130, and any other exception is the program's own failure, exit 2 with the type.
A provider that decides on a fact reads it from a check: `observed(probe_now(s3.bucket_status(
name, identity=identity)))` returns the item's observed value, or raises `Unmet` when the check
could not observe it.
````

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'EOF'
feat(core): Gate.checked runs a program's guards and keeps its providers alive for the block

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

---

### Task 2: `s3.bucket_status` and `s3.object_exists`; version 0.2.0

**Files:**
- Create: `src/preflight/catalog/s3.py`, `tests/test_catalog_s3.py`
- Modify: `README.md` (catalog table), `pyproject.toml` and `src/preflight/__init__.py` (version)

**Interfaces:**
- Produces: `s3.bucket_status(bucket: BucketName, identity: Identity)` (`key="bucket"`) observing `"present" | "absent" | "forbidden"`; `s3.object_exists(bucket: BucketName, key: str, identity: Identity)` (`key="key"`) observing `True | False`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_catalog_s3.py`:

```python
import pytest
from fakes import IDENTITY, FakeAnsibleHost, FakeHost, FakeResult, make_probe, observe
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import s3
from preflight.check import CheckCallError
from preflight.outcome import Status


def head(rc, stderr=""):
    return FakeHost([("s3api head-bucket", FakeResult(rc, "", stderr))])


@pytest.mark.parametrize(
    ("host", "status", "observed"),
    [
        (head(0), Status.OK, "present"),
        (head(254, "An error occurred (404) when calling the HeadBucket operation: Not Found"), Status.OK, "absent"),
        (head(254, "An error occurred (403) when calling the HeadBucket operation: Forbidden"), Status.OK, "forbidden"),
        (head(254, "Unable to locate credentials"), Status.ERROR, None),
        (head(127), Status.ERROR, None),
    ],
)
def test_bucket_status(tmp_path, host, status, observed):
    bound = s3.bucket_status(bucket="tellabs-tfstate-dev", identity=IDENTITY)
    item = observe(bound, make_probe(tmp_path, host=host)).items[0]
    assert (item.status, item.observed) == (status, observed)
    assert "--bucket tellabs-tfstate-dev" in host.commands[0]
    assert bound.label == "s3.bucket_status(tellabs-tfstate-dev)"


def test_bucket_status_error_steps(tmp_path):
    missing = observe(s3.bucket_status(bucket="b", identity=IDENTITY), make_probe(tmp_path, host=head(127)))
    assert missing.items[0].error_type == "MissingTool"
    other = observe(s3.bucket_status(bucket="b", identity=IDENTITY), make_probe(tmp_path, host=head(254, "SECRET detail")))
    assert other.items[0].error_type == "AwsCli"
    assert "SECRET" not in other.items[0].next_step.do
    assert "profile sandbox" in other.items[0].next_step.do


def test_object_exists(tmp_path):
    present = FakeAnsibleHost({"amazon.aws.s3_object": {"s3_keys": ["platform/bootstrap.tfstate"]}})
    bound = s3.object_exists(bucket="b", key="platform/bootstrap.tfstate", identity=IDENTITY)
    assert observe(bound, make_probe(tmp_path, ansible=present)).items[0].observed is True
    assert present.calls == [
        ("amazon.aws.s3_object", {"bucket": "b", "mode": "list", "prefix": "platform/bootstrap.tfstate", "max_keys": 1, "region": "us-east-1", "profile": "sandbox"})
    ]
    absent = FakeAnsibleHost({"amazon.aws.s3_object": {"s3_keys": []}})
    assert observe(bound, make_probe(tmp_path, ansible=absent)).items[0].observed is False
    sibling = FakeAnsibleHost({"amazon.aws.s3_object": {"s3_keys": ["platform/bootstrap.tfstate.backup"]}})
    assert observe(bound, make_probe(tmp_path, ansible=sibling)).items[0].observed is False
    failing = FakeAnsibleHost({"amazon.aws.s3_object": AnsibleException({"failed": True, "msg": "SECRET"})})
    item = observe(bound, make_probe(tmp_path, ansible=failing)).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "AnsibleException")
    assert bound.label == "s3.object_exists(platform/bootstrap.tfstate)"


def test_bucket_names_are_validated():
    with pytest.raises(CheckCallError, match="bucket"):
        s3.bucket_status(bucket="Not A Bucket", identity=IDENTITY)
```

(Wrap the long parametrize strings across lines to satisfy ruff.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_catalog_s3.py -q` → ImportError.

- [ ] **Step 3: Write `src/preflight/catalog/s3.py`**

```python
"""S3 facts a provider decides on: whether a bucket exists (or belongs to someone else) and
whether an object exists. Observations, not verdicts: an item is ok with what was seen."""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import Outcome, error, ok, outcome
from preflight.probe import Probe

BucketName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")]


@check(key="bucket")
def bucket_status(probe: Probe, bucket: BucketName, identity: Identity) -> Outcome:
    """"present", "absent", or "forbidden" (the name belongs to another account). head-bucket
    is the one call that tells a 403 from a 404, and no Ansible module exposes it."""
    result = probe.host.run(
        "aws s3api head-bucket --bucket %s --region %s", bucket, identity.region
    )
    if result.rc == 127:
        return outcome(error(do="Install the AWS CLI.", error_type="MissingTool"))
    if result.rc == 0:
        return outcome(ok(observed="present"))
    if "(404)" in result.stderr:
        return outcome(ok(observed="absent"))
    if "(403)" in result.stderr:
        return outcome(ok(observed="forbidden"))
    return outcome(
        error(
            do=(
                f"Could not tell whether s3://{bucket} exists; check that profile "
                f"{identity.profile} is signed in and may call s3:ListBucket."
            ),
            error_type="AwsCli",
        )
    )


@check(key="key")
def object_exists(probe: Probe, bucket: BucketName, key: str, identity: Identity) -> Outcome:
    try:
        listing = probe.aws_module(
            "amazon.aws.s3_object",
            {"bucket": bucket, "mode": "list", "prefix": key, "max_keys": 1},
            expect=("s3_keys",),
        )
    except Exception as exc:
        return outcome(
            error(
                do=(
                    f"Could not list s3://{bucket}/{key}; check that profile "
                    f"{identity.profile} may call s3:ListBucket."
                ),
                error_type=type(exc).__name__,
            )
        )
    return outcome(ok(observed=key in (listing.get("s3_keys") or [])))
```

- [ ] **Step 4: Version and README**

Set `version = "0.2.0"` in `pyproject.toml` and `__version__ = "0.2.0"` in `src/preflight/__init__.py`; run `uv lock`. In the README catalog table add:

```markdown
| `s3.bucket_status` | `bucket`, `identity` | Observes `present`, `absent` or `forbidden` (another account owns the name) |
| `s3.object_exists` | `bucket`, `key`, `identity` | Observes whether the object exists |
```

and add `aws` CLI to the tools list for `s3.bucket_status`.

- [ ] **Step 5: Run everything and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

```bash
git add -A
git commit -F - <<'EOF'
feat(catalog): s3.bucket_status and s3.object_exists, observations for a program's decision

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

**Operator gate after Task 2:** push `feat/scoped-runs`, open the PR, merge, tag `v0.2.0`. Task 3 pins the tag (or the pushed commit until the tag exists).

---

### Task 3: iac — `scripts/bootstrap.py`

In `~/Develop/tellabs/iac-preflight-bootstrap`, on a new branch `feat/bootstrap-program` from `origin/main` (after iac#10 merges; otherwise from `chore/preflight-v0.1.0`).

**Files:**
- Create: `scripts/bootstrap.py`, `scripts/tests/test_bootstrap_program.py`
- Modify: `preflight/iac.py`, `preflight/bootstrap.py`, `Taskfile.yml`, `.github/workflows/platform.yml`, `README.md`, `AGENTS.md`
- Delete: `scripts/bootstrap.sh`, `preflight/bootstrap_entry.py`

**Interfaces:**
- Consumes: `Gate.checked`, `Run`, `observed`, `probe_now`, `Unmet`, `s3.bucket_status`, `s3.object_exists`, `aws.assumed`, `aws.region`, `git.up_to_date`, `iac.Env`, `iac.Admin`, `iac.ids_filled`, `iac.tfvars`, `iac.ROOT`.
- Produces (in `preflight/iac.py`): `bootstrap_published: Guards`, `state_plan_known` check.

- [ ] **Step 1: Move the milestone guard into `iac.py`**

In `preflight/iac.py`, add after `ids_filled`:

```python
bootstrap_published = Guards()


@bootstrap_published.guard("bootstrap published")
def published(env: Env, identity: Admin):
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


@check
def state_plan_known(
    probe: Probe, bucket: str, bucket_status: str, local_state: bool, object_exists: bool
) -> Outcome:
    """Restates the bootstrap's decision as an item, so the worklist shows what was decided."""
    first = bucket_status == "absent" or local_state
    plan = "first run: apply with local state, then migrate it" if first else "rerun against the remote state"
    return outcome(
        ok(observed={"bucket": bucket, "status": bucket_status, "local_state": local_state,
                     "state_object": object_exists, "plan": plan})
    )
```

(import `ssm` and `tofu` from `preflight.catalog`.) Then `preflight/bootstrap.py` becomes:

```python
gate = Gate("bootstrap")
gate.include(iac.ids_filled)
gate.include(iac.bootstrap_published)

if __name__ == "__main__":
    gate.run()
```

with its header pinned to `v0.2.0` (or the pushed commit) and `python-hcl2>=8,<9`.

- [ ] **Step 2: Write `scripts/bootstrap.py`**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "preflight @ git+https://github.com/strider4560/preflight@v0.2.0",
#   "python-hcl2>=8,<9",
# ]
# ///
"""Bootstrap of one account, run from a workstation with SSO administrator credentials for that
account (AWS_PROFILE=sandbox or production). Safe to rerun, and the only way to change
stacks/bootstrap after the first run:

- First run (the state bucket does not exist yet): applies stacks/bootstrap with local state,
  then switches the stack to the bucket it just created and migrates the state into it.
- Later runs (the bucket exists): inits against the S3 backend and applies against the remote
  state, so nothing is created twice. A local state left by a first run that stopped before the
  migration is migrated first.

Either way the apply asks for approval, and the run ends with the bootstrap milestone's guards.

    scripts/bootstrap.py dev
    scripts/bootstrap.py prod
"""

import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from preflight import Depends, Gate, Unmet, fail, observed, probe_now
from preflight.catalog import aws, git, s3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "preflight"))
import iac  # noqa: E402

STACK = iac.ROOT / "stacks" / "bootstrap"
BACKEND = STACK / "backend.tf"
BACKEND_OFF = STACK / "backend.tf.off"
LOCAL_STATE = STACK / "terraform.tfstate"


@dataclass(frozen=True)
class StatePlan:
    bucket: str
    key: str
    bucket_status: str
    local_state: bool
    object_exists: bool

    @property
    def first_run(self) -> bool:
        return self.bucket_status == "absent" or self.local_state


def state_key() -> str:
    match = re.search(r'^\s*key\s*=\s*"([^"]+)"', BACKEND.read_text(), re.MULTILINE)
    if match is None:
        raise ValueError("backend.tf has no key")
    return match[1]


def state_plan(env: iac.Env, identity: iac.Admin) -> StatePlan:
    values = iac.tfvars(f"envs/{env}.tfvars")
    bucket = f"tellabs-tfstate-{env}{values.get('state_bucket_suffix', '')}"
    key = state_key()
    status = observed(probe_now(s3.bucket_status(bucket=bucket, identity=identity)))
    local = LOCAL_STATE.exists()
    exists = (
        observed(probe_now(s3.object_exists(bucket=bucket, key=key, identity=identity)))
        if status == "present"
        else False
    )
    if status == "forbidden":
        raise Unmet(
            fail(
                do=(
                    f"s3://{bucket} belongs to another account; set state_bucket_suffix in "
                    f"envs/{env}.tfvars and rerun."
                )
            )
        )
    if status == "present" and local and exists:
        raise Unmet(
            fail(
                do=(
                    f"s3://{bucket}/{key} already exists and so does the local state in "
                    f"stacks/bootstrap; refusing to overwrite it. Compare the two by hand, delete "
                    "the stale one, then rerun."
                )
            )
        )
    if status == "present" and not local and not exists:
        raise Unmet(
            fail(
                do=(
                    f"s3://{bucket} exists but holds no {key} and there is no local state; "
                    "import the existing resources by hand instead of applying."
                )
            )
        )
    return StatePlan(bucket, key, status, local, exists)


Plan = Annotated[StatePlan, Depends(state_plan)]

gate = Gate("bootstrap")
gate.include(iac.ids_filled)


@gate.guard("this shell is the account's administrator")
def administrator(identity: iac.Admin):
    return [aws.assumed(identity=identity), aws.region(identity=identity)]


@gate.guard("the checkout holds the latest main")
def current():
    return [git.up_to_date(remote="origin", branch="main")]


@gate.guard("the state is where the plan expects it")
def state(plan: Plan):
    return [
        iac.state_plan_known(
            bucket=plan.bucket,
            bucket_status=plan.bucket_status,
            local_state=plan.local_state,
            object_exists=plan.object_exists,
        )
    ]


def tofu(*args: str, run=subprocess.run) -> None:
    """Runs tofu with inherited stdio. On Ctrl-C the terminal has already sent tofu SIGINT, so
    keep waiting for it to finish its own shutdown instead of letting subprocess.run SIGKILL it
    a quarter second later (which would leave a stale state lock behind)."""
    if run is not subprocess.run:
        run(["tofu", *args], cwd=STACK, check=True)
        return
    process = subprocess.Popen(["tofu", *args], cwd=STACK)
    try:
        code = process.wait()
    except KeyboardInterrupt:
        # Ctrl-C already reached tofu (same process group); a SIGTERM or SIGHUP to this program
        # alone did not. Give tofu time to finish its own shutdown, then interrupt it ourselves.
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            code = process.wait()
        raise
    if code != 0:
        raise subprocess.CalledProcessError(code, ["tofu", *args])


def var_files(env: str) -> list[str]:
    return [
        f"-var-file={iac.ROOT}/envs/{env}.tfvars",
        f"-var-file={STACK}/envs/{env}.tfvars",
        f"-var-file={STACK}/envs/common.tfvars",
    ]


def restore_backend() -> None:
    """A run killed mid-way can leave backend.tf.off behind; put it back."""
    if BACKEND_OFF.exists():
        BACKEND_OFF.replace(BACKEND)


def apply_first_run(env: str, plan: StatePlan, run=subprocess.run) -> None:
    if plan.bucket_status == "absent":
        print(f"s3://{plan.bucket} does not exist: first bootstrap of {env} with local state")
    else:
        print(f"s3://{plan.bucket} exists and so does local state: finishing the interrupted first bootstrap of {env}")
    if BACKEND.exists():
        BACKEND.replace(BACKEND_OFF)
    shutil.rmtree(STACK / ".terraform", ignore_errors=True)
    tofu("init", "-input=false", *var_files(env), run=run)
    tofu("apply", *var_files(env), run=run)  # interactive approval on purpose: this is a hand apply
    BACKEND_OFF.replace(BACKEND)
    tofu("init", "-input=false", "-migrate-state", "-force-copy", *var_files(env), run=run)
    for name in ("terraform.tfstate", "terraform.tfstate.backup"):
        (STACK / name).unlink(missing_ok=True)
    print("state migrated")


def apply_rerun(env: str, plan: StatePlan, run=subprocess.run) -> None:
    print(f"s3://{plan.bucket} exists: applying {env} against the remote state")
    shutil.rmtree(STACK / ".terraform", ignore_errors=True)
    tofu("init", "-input=false", "-reconfigure", *var_files(env), run=run)
    tofu("apply", *var_files(env), run=run)  # interactive approval on purpose: this is a hand apply


def apply(env: str, plan: StatePlan, run=subprocess.run) -> None:
    restore_backend()
    try:
        if plan.first_run:
            apply_first_run(env, plan, run=run)
        else:
            apply_rerun(env, plan, run=run)
    finally:
        restore_backend()


if __name__ == "__main__":
    with gate.checked(sys.argv[1:]) as run:
        apply(run.args["env"], run[state_plan])
        run.verify(iac.bootstrap_published)
```

`chmod +x scripts/bootstrap.py`. A `CalledProcessError` from `tofu apply` (the operator answered `no`, or the apply failed) leaves the block as the program's failure: exit 2 with `the program raised CalledProcessError`, after `restore_backend()`.

- [ ] **Step 3: Unit-test the helpers**

Create `scripts/tests/test_bootstrap_program.py` (the iac script tests run with `.venv/bin/pytest`, which does not have preflight installed, so load only the helpers):

```python
"""The bootstrap program's own logic: the state decision and backend.tf handling. The guards
themselves are preflight's; `--validate` covers them in task check."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "bootstrap.py"


def load(monkeypatch, tmp_path):
    """Loads the program with preflight and iac stubbed, pointing its STACK at tmp_path."""
    preflight = types.ModuleType("preflight")
    for name in ("Depends", "Gate", "Unmet", "fail", "observed", "probe_now"):
        setattr(preflight, name, lambda *a, **k: None)

    class Gate:
        def __init__(self, name): ...
        def include(self, g): ...
        def guard(self, name):
            return lambda fn: fn

    preflight.Gate = Gate
    catalog = types.ModuleType("preflight.catalog")
    for name in ("aws", "git", "s3"):
        setattr(catalog, name, types.SimpleNamespace())
    iac = types.SimpleNamespace(ROOT=tmp_path, Env=str, Admin=str, ids_filled=None, tfvars=lambda p: {}, state_plan_known=lambda **k: None)
    monkeypatch.setitem(sys.modules, "preflight", preflight)
    monkeypatch.setitem(sys.modules, "preflight.catalog", catalog)
    monkeypatch.setitem(sys.modules, "iac", iac)
    spec = importlib.util.spec_from_file_location("bootstrap_program", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def program(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path)
    module.STACK.mkdir(parents=True)
    (module.STACK / "envs").mkdir()
    module.BACKEND.write_text('terraform {\n  backend "s3" {\n    key = "platform/bootstrap.tfstate"\n  }\n}\n')
    return module


def test_state_key_and_first_run_rule(program):
    assert program.state_key() == "platform/bootstrap.tfstate"
    plan = program.StatePlan
    assert plan("b", "k", "absent", False, False).first_run
    assert plan("b", "k", "present", True, False).first_run
    assert not plan("b", "k", "present", False, True).first_run


def test_first_run_moves_the_backend_aside_and_back(program):
    calls = []

    def run(cmd, cwd, check):
        calls.append(cmd[1:3])
        if cmd[1] == "apply":
            assert not program.BACKEND.exists() and program.BACKEND_OFF.exists()

    program.apply("dev", program.StatePlan("b", "k", "absent", False, False), run=run)
    assert [c[0] for c in calls] == ["init", "apply", "init"]
    assert "-migrate-state" in calls[2]
    assert program.BACKEND.exists() and not program.BACKEND_OFF.exists()


def test_backend_is_restored_after_a_failed_apply(program):
    def run(cmd, cwd, check):
        if cmd[1] == "apply":
            raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(subprocess.CalledProcessError):
        program.apply("dev", program.StatePlan("b", "k", "absent", False, False), run=run)
    assert program.BACKEND.exists() and not program.BACKEND_OFF.exists()


def test_rerun_reconfigures_and_never_touches_the_backend(program):
    calls = []
    program.apply("prod", program.StatePlan("b", "k", "present", False, True), run=lambda cmd, cwd, check: calls.append(cmd))
    assert [c[1] for c in calls] == ["init", "apply"]
    assert "-reconfigure" in calls[0]
```

Run: `task py:test` → passes.

- [ ] **Step 4: Retire the shell and the entry gate; wire validation and docs**

- `git rm scripts/bootstrap.sh preflight/bootstrap_entry.py`.
- `Taskfile.yml` `preflight:validate`: the loop body becomes `uv run --script preflight/bootstrap.py {{.ITEM}} --validate` and `uv run --script scripts/bootstrap.py {{.ITEM}} --validate`; the CI step's loop likewise.
- `README.md`: every `scripts/bootstrap.sh` becomes `scripts/bootstrap.py`; the layout row for `preflight/` drops `bootstrap_entry.py` and says `scripts/bootstrap.py` runs its guards, then applies, then verifies the milestone; the bootstrapping paragraph says the run ends with the milestone's guards rather than a clean-plan check. `AGENTS.md`: the same rename in its Commands paragraph.
- Run `task preflight:validate` (four runs pass), `shellcheck` is no longer needed for the removed script, `task py:test`, `.venv/bin/python scripts/pin_actions.py --check .github/workflows`.

- [ ] **Step 5: Live acceptance (controller, with the operator's SSO sessions)**

| Command | Expected |
|---|---|
| `AWS_PROFILE=production scripts/bootstrap.py dev` | stops at "this shell is the account's administrator", exit 1, nothing applied |
| `AWS_PROFILE=sandbox scripts/bootstrap.py dev` with `no` piped to the approval prompt (`printf 'no\n' \| …`) | guards pass; "the state is where the plan expects it" ok; "rerun against the remote state" printed; `tofu init -reconfigure` then `tofu apply` asks for approval and stops on `no`; exit 2 with `the program raised CalledProcessError`; `backend.tf` present, no `backend.tf.off` |
| `uv run --script preflight/bootstrap.py dev` and `prod` | every guard passed |

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -F - <<'EOF'
feat(bootstrap): bootstrap.sh becomes a preflight program

scripts/bootstrap.py runs the entry guards, decides first run or rerun
from what it observes of the state bucket, applies with the shell's
tofu steps one for one, and ends with the bootstrap milestone's guards.
The entry gate and the shell script are retired.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DxemcQrEeZ5VBGDz9RCUXZ
EOF
```

Pushing and opening the pull request are operator-approved steps.
