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
| 130 | Interrupted (Ctrl-C, SIGTERM or SIGHUP); workers are killed and providers clean up |

`--help` exits 2, so a wrapper that stops on a nonzero exit never proceeds on it.

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
