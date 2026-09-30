# Preflight gates as programs — design

| | |
|---|---|
| Date | 2026-09-29 |
| Status | Draft, awaiting operator review |
| Supersedes | `2026-09-29-preflight-library-design.md` (contracts, sections, the `preflight` command) |
| Design driver | `tellabsadmin/iac` (`~/Develop/tellabs/iac`), its `scripts/bootstrap.sh`, and PR tellabsadmin/iac#9 |

## Why

The first design made preflight own the consumer's configuration. A TOML contract per environment held references into the consumer's files (`tfvars`, `yaml`, `json`, lazy `ssm`), placeholder lists, `how` strings and `[section.remedy]` tables; a `preflight` command loaded a gate, the contract and every gate it required, and a `status` command ran every milestone gate against every contract. Building iac's bootstrap gates on it (PR #9) showed the cost. The contracts became a place for strings: remediation text, parameter names and branch names that are the same in every environment. Preflight had to understand HCL to read values the consumer already knows how to read. And a gate could not express order beyond "requires another gate".

Preflight is an assertion framework. The gate is the program: it takes its own inputs, reads values from wherever the consumer keeps them, and orders its assertions. Preflight runs the assertions safely and says what to do next.

## Intent

What the operator said:

- Preflight is an assertion framework, not a configuration system. It has no knowledge of HCL or any other consumer format; the consumer pulls check values from anything, such as an HCL file.
- There is no CLI. A gate script invokes the library, and that script is the whole entry point to the checks performed at the gate. There is no `status` command.
- The gate file does the setup and orchestration.
- Checks are called with their inputs as arguments, for example `ssm.parameters_exist([...])`, and provide their own failure and remediation messages with the values templated in. No `how`, no `remedy` tables.
- Guards run in sequence; a failure in one does not proceed to the next.
- The API follows FastAPI, to inherit its design experience: decorated functions, dependencies, `yield` dependencies for setup and teardown, routers, overrides for testing.

Carried over from the first design:

- Preflight never runs what it gates and never changes what it observes. A consumer's script calls the gate first and stops unless it exits 0.
- Probes go through testinfra and, through its Ansible backend, Ansible modules, so the operator does not track how vendors' commands and responses change. Only where no module can observe a fact does a check use a library directly (DNS referrals).
- Each check runs in its own worker process with a cleaned environment and a timeout that kills its whole process group.
- Every open step names the next thing to do, with text to paste. The catalog never reads secret values.

## The FastAPI mapping

| FastAPI | Preflight |
|---|---|
| `app = FastAPI()` | `gate = Gate("bootstrap")` |
| A route handler, `@app.get(...)` | `@gate.guard("bootstrap published")`: a function returning the checks to run |
| Path and query parameters, validated by pydantic | Gate inputs from the command line, `Annotated[T, Arg()]`, as Typer does |
| `Depends`, resolved once per request | `Depends`, resolved once per run |
| A dependency with `yield` | Setup and teardown around the run: before `yield` sets up and verifies, after `yield` always runs |
| `HTTPException` | `Unmet`: a dependency cannot be provided; carries the next steps |
| `APIRouter`, `include_router` | `Guards()`, `gate.include(...)` |
| `app.dependency_overrides`, `TestClient` | `gate.dependency_overrides`, `GateClient` |
| `uvicorn.run(app)` | `gate.run()` |

## A gate

A gate is an executable Python file in the consumer's repository. Its PEP 723 header declares preflight, pinned, and whatever the gate itself uses; `uv` provides them, so the consumer needs no Python project.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "preflight @ git+https://github.com/strider4560/preflight@v0.1.0",
#   "python-hcl2>=8,<9",
# ]
# ///
"""Bootstrap is applied: its parameters are published and stacks/bootstrap plans clean."""

import iac
from preflight import Gate
from preflight.catalog import ssm, tofu

gate = Gate("bootstrap")
gate.include(iac.ids_filled)


@gate.guard("bootstrap published")
def published(env: iac.Env, identity: iac.Admin):
    return [
        ssm.parameters_exist(
            ["/platform/state/bucket", "/platform/state/kms_key_arn", "/platform/oidc/provider_arn"],
            identity=identity,
        ),
        tofu.plan_clean(
            "stacks/bootstrap",
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

A consumer script runs it and stops on anything but 0:

```bash
uv run "$root/preflight/bootstrap_entry.py" "$env" || exit 1
```

### Guards

- `@gate.guard(name)` registers a guard. Guard names are unique within a gate, including included guards; a repeat is a gate error.
- A guard function returns a list of bound checks (below). It is called only when its turn comes, after its dependencies are resolved. Returning anything else, or an empty list, is a gate error.
- `Guards()` is a router: `shared = Guards()`, `@shared.guard(...)`. `gate.include(shared)` places its guards where the call appears, in their declaration order. This is how two gates share guards.

### A run

`gate.run()`:

1. **Parses the command line.** Every `Arg` declared by any guard or dependency the gate can reach becomes a command-line parameter, validated by pydantic. As in Typer, a parameter without a default is positional and one with a default is an `--option`; the same name declared with two different types is a gate error. `--validate` is reserved (see Validation). A parse or validation failure prints usage and exits 2.
2. **Runs the guards in declaration order.** For each guard:
   1. resolves its parameters (see Dependencies); an `Unmet` stops the run at this guard;
   2. calls the guard function, which validates each check's arguments;
   3. runs its checks in parallel, at most `Gate(jobs=4)` workers at once;
   4. stops the run if any blocking item is not `ok` (`fail`, `pending` or `error`). Advisory items never stop it.
3. **Cleans up.** The code after `yield` in every entered dependency runs, in reverse order of entry, whether the run passed or stopped.
4. **Prints the worklist and exits.**

| Exit | Meaning |
|---|---|
| 0 | Every guard passed |
| 1 | A guard stopped the run |
| 2 | The gate is wrong: a bad command-line argument, a check called with the wrong types, a guard returning something other than checks, a duplicate guard name or `Arg` conflict, or an exception from the gate's own code (a guard function or a provider) |
| 3 | Preflight itself failed |
| 130 | Interrupted: every worker's process group is killed, cleanup runs, nothing is reported ok |

An exception is reported by where it happened and its type only (`provider admin raised KeyError`), because its message may carry a value the gate read. A cleanup that raises is reported after the worklist; it turns a passing run into exit 3 and leaves any other exit code unchanged.

### The worklist

```
preflight bootstrap_entry dev

  ✓ IDs filled in
  ✗ this shell is the account's administrator
      FAIL  aws.assumed
            Your shell acts as arn:aws:sts::503561451926:assumed-role/… in account 503561451926;
            the next command needs role AWSReservedSSO_AWSAdministratorAccess_* in account 711387098919.
              export AWS_PROFILE=sandbox
      ok    aws.region

Stopped at: this shell is the account's administrator
Not run: the checkout holds the latest main
```

Passed guards are one line each. The stopping guard lists every item: first the items that are not ok in the order its checks were returned, each with its next step (`do`, then `paste`, `wait` and `ref` when present), then its ok items. Advisory items, from any guard that ran, are listed as warnings after the stopping guard. The guards that did not run are named, which a declarative gate knows before running anything. Steps with identical rendered `do` and `paste` are merged. Output goes to stdout; there is no JSON or JUnit report.

## Checks

A check is a function decorated with `@check`:

```python
from preflight import Outcome, Probe, check, fail, ok, outcome


@check(key="file")
def filled(probe: Probe, file: str, keys: list[str], placeholders: list[str]) -> Outcome:
    values = tfvars(probe.root / file)
    return outcome(
        *(fail(k, do=f"Fill {k} in {file}.") if values.get(k) in placeholders else ok(k) for k in keys)
    )
```

- **Calling** a check does not run it. The call validates the arguments against the function's type hints with pydantic, as `validate_call` does, and returns a bound check. A wrong type raises at the call, which surfaces as exit 2 before anything is observed. Arguments must be JSON-serializable after validation, because they travel to the worker.
- Every call also accepts the reserved keyword `timeout=` (seconds), overriding the check's default. `@check(timeout=...)` sets the default: 60 seconds, 600 for `tofu.plan_clean`.
- **Ids.** A check's id is its module's last name component and its function name: `ssm.parameters_exist`, `iac.filled`, `bootstrap.filled` for a check defined in the gate file `bootstrap.py`. `@check(key="file")` names the argument that identifies a call in the worklist: `iac.filled(envs/dev.tfvars)`. Without `key`, the worklist shows the id alone and numbers repeats within a guard. Item keys extend it: `iac.filled(envs/dev.tfvars):account_id`.
- **The probe** is the observation handle, the first parameter, supplied by the worker: `probe.host` (testinfra, `local://`), `probe.ansible` (testinfra's Ansible backend with a generated local inventory), `probe.aws_module(module, args, expect=...)`, `probe.ssm_lookup(name)`, `probe.dns`, `probe.root` (the git top level of the gate file's directory) and `probe.environ`.
- **Outcomes** are unchanged: an `Outcome` of items, each `ok`, `fail` (the operator has something to do), `pending` (done, settling) or `error` (could not observe), optionally `advisory`. The next step is the check's own, rendered from its arguments and observations. There is no `how`, no remedy table and no `generic` flag. A check returning anything other than an `Outcome`, or raising, is an `error` carrying only the exception type.

### Workers

Each bound check runs in its own worker process, as in the first design:

- started as `python -P -m preflight.worker` with the gate's interpreter, in a new session, with the repository root as its working directory;
- the job is JSON on stdin: the check's module name, the file to load it from when the module is the gate itself, the gate's directory, and the validated arguments;
- the worker appends the gate's directory to `sys.path`, so a gate's own modules (`iac`) import in the worker as they do in the gate, without shadowing preflight's imports; it loads a gate file by path under its own module name, where `__name__` is not `"__main__"`, so `gate.run()` does not fire (uvicorn loads `main:app` the same way);
- the environment is cleaned as before: AWS credential and profile variables, `TF_CLI_ARGS*`, `TF_WORKSPACE`, `TF_VAR_*`, endpoint overrides and container-credential variables are removed. An argument of type `aws.Identity` then sets `AWS_PROFILE`, `AWS_REGION` and `AWS_DEFAULT_REGION` from it; a check may take at most one. `@check(ambient=True)` (only `aws.assumed`) keeps the caller's variables;
- the worker returns only a serialized `Outcome`; a timeout kills its process group and records `error` ("timed out after N s").

## Dependencies

A guard's parameters, and a provider's, are resolved as FastAPI resolves a handler's:

```python
# iac.py
Env = Annotated[Literal["dev", "prod"], Arg(help="the environment: dev (Sandbox) or prod (Production)")]
PROFILES = {"dev": "sandbox", "prod": "production"}


def admin(env: Env) -> Iterator[aws.Identity]:
    tf = tfvars(f"envs/{env}.tfvars")
    with aws.signed_in(
        profile=PROFILES[env],
        region=tf["region"],
        account_id=tf["account_id"],
        permission_set="AWSAdministratorAccess",
    ) as identity:
        yield identity


Admin = Annotated[aws.Identity, Depends(admin)]
```

- **`Arg()`** marks a command-line input; its type is the annotation, its name the parameter's name.
- **`Depends(provider)`** calls `provider`, resolving its own parameters the same way. The result is cached for the run, keyed by the provider function, as FastAPI caches per request. A typed dependency is an alias, `Admin = Annotated[aws.Identity, Depends(admin)]`, reused across guards, gates and routers.
- **Resolution is lazy.** A provider runs when the first guard that needs it is about to run. In the entry gate, the guard that checks the IDs are filled in therefore runs before `admin` reads an account ID that may still be a placeholder.
- **Providers run in the gate process.** They are the consumer's own code. Anything that observes goes through a worker: `probe_now(bound_check)` runs one check synchronously in a worker and returns its `Outcome`.
- **`Unmet`** is raised by a provider that cannot provide. It carries items with next steps (`Unmet(fail(do=..., paste=...))`, or the items of an `Outcome`). The guard that needed the provider stops the run; its items appear under that guard, and the guard function is never called.
- **`yield` providers** are entered when resolved; after the run, their code after `yield` runs in reverse order of entry.
- Any other exception from a provider is a gate error (exit 2).

### `aws.Identity` and `aws.signed_in`

`aws.Identity` is a pydantic model: `profile`, `region`, `account_id`, and exactly one of `role` (an exact IAM role name) or `permission_set` (an IAM Identity Center permission set, matching `AWSReservedSSO_<permission_set>_<16 hex digits>` in full). The caller must be an assumed role in `account_id`; IAM users and root never match. These are the first design's identity rules, moved from `identity.py`.

`aws.signed_in(**fields)` is the catalog's one provider helper, a context manager: it builds the `aws.Identity`, observes the profile's caller with `probe_now` (today's `aws.session` check), and yields the identity, or raises `Unmet` with that check's items, for example "Sign in to profile sandbox" with `aws sso login --profile sandbox` to paste. Other observing providers, such as an `ssm.value`, wait until a gate needs one.

## Catalog

The checks and their observation code carry over; their inputs become arguments.

| Check | Arguments | What it proves |
|---|---|---|
| `aws.assumed` | `identity` | The caller's own shell acts as the identity (ambient) |
| `aws.region` | `identity` | The profile's configured region is the identity's |
| `ssm.parameters_exist` | `names`, `identity` | Each parameter exists and is non-empty, read without decryption; a `SecureString` is an error. Replaces `ssm.present` and `ssm.parameters`. A missing one: "SSM parameter /platform/state/bucket does not exist in account 711387098919 (us-east-1). Publish it from the stack that owns it", with `aws ssm get-parameter` to confirm afterwards |
| `acm.issued` | `arn`, `identity` | Issued; or the exact validation CNAME to add; or waiting |
| `tofu.plan_clean` | `dir`, `var_files`, `identity` | `tofu plan -detailed-exitcode` is 0, read-only; refuses a directory holding `backend.tf.off` or `terraform.tfstate` |
| `dns.delegated` | `root`, `name_servers` (zone prefix → servers) | The parent's own servers delegate exactly those servers |
| `dns.undelegated` | `root`, `zones` | The parent gives no referral |
| `dns.cname`, `dns.caa` | their former section fields | Records the operator adds by hand |
| `github.auth`, `repo`, `variables`, `environments`, `ruleset`, `secret_names`, `workflow_green` | their former section fields | GitHub settings; secrets by name only |
| `git.up_to_date` | `remote="origin"`, `branch="main"` | The checkout contains the remote branch's latest commit, without fetching |
| `files.present`, `absent`, `git_ignored`, `committed` | `paths` | Existence and git status only |
| `sops.rule` | its former section fields | A creation rule with enough age recipients covers each secrets file |

`aws.session` stays as a check, used by `aws.signed_in`.

## Validation

`gate.run()` reserves `--validate`: `uv run preflight/bootstrap.py dev --validate` needs no credentials and observes nothing.

- It parses the command line and resolves every provider every guard needs, so the consumer's own reading runs: a renamed tfvars key or a moved file fails here.
- `probe_now` returns a stand-in `ok` outcome without starting a worker, so `aws.signed_in` yields its identity unverified.
- It calls every guard function, which validates every check's arguments.
- It exits 0, or 2 for a gate error. No worker starts.

A gate whose guards depend on observed values (an `ssm.value` provider, later) validates with stand-ins for those values; the gate's author decides whether that is meaningful.

## Testing

```python
from preflight.testing import GateClient

gate.dependency_overrides[iac.admin] = lambda: aws.Identity(
    profile="sandbox", region="us-east-1", account_id="711387098919",
    permission_set="AWSAdministratorAccess",
)
client = GateClient(gate, outcomes={ssm.parameters_exist: outcome(fail("/platform/state/bucket", do="…"))})
result = client.run(["dev"])
assert result.exit_code == 1
assert result.stopped_at == "bootstrap published"
```

`GateClient` runs the gate in process, replacing each named check's result by function; a check without a stand-in is an error in the test, so no worker starts and nothing is observed. `dependency_overrides` replaces providers as in FastAPI.

The library's own tests stay offline:

- the worker, outcome and catalog tests carry over, adapted to argument-based checks; every `fail`, `pending` and `error` test asserts on the rendered next step;
- new tests: guard order and stopping; `include`; `Arg` parsing, usage and conflicts; `Depends` caching and laziness; `yield` cleanup order, on pass, stop and interrupt; `Unmet`; every exit code; `--validate`; a check defined in the gate file loading in a worker;
- end to end: a gate script run with `uv run --script` against a scratch git repository, through real workers and testinfra, without network;
- `ruff check` and `ruff format --check`.

## The library

| Unit | Purpose |
|---|---|
| `gate.py` | `Gate`, `Guards`, guard registration, `include`, `run()` |
| `params.py` | `Arg`, `Depends`, command-line parsing, dependency resolution and caching, `Unmet` |
| `check.py` | `@check`, bound checks, ids and keys |
| `probe.py` | `Probe` (formerly `context.py`), `probe_now` |
| `worker.py` | Runs one bound check in its own process |
| `outcome.py` | `Outcome`, items, `NextStep` (without `generic`), `ok`/`fail`/`pending`/`error` |
| `render.py` | The terminal worklist |
| `testing.py` | `GateClient` |
| `dnsclient.py` | Unchanged |
| `catalog/` | The catalog above; `aws.Identity` and `aws.signed_in` in `catalog/aws.py` |

Removed: `cli.py` and the `preflight` console script, `contract.py`, `resolvers.py`, `graph.py`, the graph-based `runner.py`, `identity.py` (its rules move to `aws.Identity`), JSON and JUnit rendering, and the `python-hcl2` dependency. PyYAML stays for `sops.rule`. The README is rewritten around the gate program.

## Release

`v0.1.0` was never tagged. This design becomes the first tag, and consumers pin it in their gates' PEP 723 headers. The tag is pushed only with the operator's approval.

## iac migration

As follow-up commits on tellabsadmin/iac#9, once the tag exists:

- `preflight/contracts/`, `preflight/gates/` and `preflight/checks/` are replaced by:
  - `preflight/iac.py`: `tfvars()` (python-hcl2), the `filled` check, `Env`, `PROFILES`, `admin` and `Admin`, and `ids_filled`, a `Guards()` holding the guard "IDs filled in" (`envs/<env>.tfvars` `account_id` against `""` and `"000000000000"`; `stacks/bootstrap/envs/common.tfvars` `github_org_id` and `iac_repo_id` against `""` and `"0"`);
  - `preflight/bootstrap_entry.py`: `ids_filled`, then "this shell is the account's administrator" (`aws.assumed`, `aws.region`), then "the checkout holds the latest main" (`git.up_to_date`);
  - `preflight/bootstrap.py`: `ids_filled`, then "bootstrap published" (above);
- `scripts/bootstrap.sh` runs `uv run "$root/preflight/bootstrap_entry.py" "$env" || exit 1`;
- `task check` runs `--validate` for both gates in both environments;
- the README's layout row and bootstrapping section describe the gates.

## Acceptance

1. Tests and lint pass.
2. With the operator's credentials, iac's gates reproduce the results recorded on PR #9: the entry gate passes for dev under `sandbox`; refuses dev under `production`, pasting `export AWS_PROFILE=sandbox`; with placeholders written into the tfvars, stops at "IDs filled in" naming each key and file; the milestone gate passes in dev and prod, including `tofu.plan_clean`; and `scripts/bootstrap.sh dev` under `production` stops at the gate before any S3 or tofu call.
3. `--validate` passes for every gate and environment without credentials, and fails with exit 2 after renaming `account_id` in a scratch copy of `envs/dev.tfvars`.

## Open items

- **A PyPI name.** Until one is chosen, gates pin preflight by git URL.
- **The age identity check.** Unchanged from the first design: checking that an age identity's public key is a recipient needs `age-keygen -y`, which reads the private key; the operator decides later.
