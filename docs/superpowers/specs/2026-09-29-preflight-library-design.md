# Preflight as an importable library — design

| | |
|---|---|
| Date | 2026-09-29 |
| Status | Draft, awaiting operator review |
| First consumer | `tellabsadmin/iac` (`~/Develop/tellabs/iac`) |
| Replaces | This repo's vendored drop-in scaffold (`gate/`, `checks/`, `contracts/`, `run.py`), its copy at `iac/preflight/`, and `iac/scripts/bootstrap_readiness.py` |

## Why

The operator juggles several projects. Weeks away from one, they no longer remember what it needs from them, and resuming becomes dangerous or tedious. In `iac` the failures cluster around work only the operator can do: DNS records in the Administration account, secrets and their age identities, IAM and GitHub settings, and cross-account dependencies that must settle between pipeline runs. The standard example: a delegated zone such as `app.tellabs.dev` lives in Sandbox while its root lives elsewhere, so the first apply fails mid-CI on certificate validation. The operator captures the failure, adds the record by hand, and reruns, knowing the second run will pass.

Preflight turns that loop into a guardrail with two jobs:

1. **Validate** the things *the operator* is responsible for, so the automations can proceed unhindered. It does not re-prove what OpenTofu or the pipeline already prove; sometimes a clean `tofu plan` is the whole check.
2. **Guide forward.** Every failure names the next step, with text ready to paste, and a single command answers "where was I?".

## Scope and decomposition

Preflight is to take over four kinds of work now done by `iac/scripts/`: readiness and guidance, CI audits of live state, static repository validation, and gating of operator actions. That is too large for one spec. It is split into:

1. **This spec — preflight core plus the first catalog, driven by iac's bootstrap.** The library, its contract/check/gate API, the guidance model, and the gates that replace `bootstrap_readiness.py` and the planned `scripts/dns_check.py`, with `bootstrap.sh` run as a gate's child command.
2. **Static repository validation** (later spec): `registry_check.py`, `env_secrets.py check`, `pin_actions.py --check`, `skills/onboard-app/scripts/onboard_check.py`. `onboard_check` ships inside a skill and runs from app repositories, which that spec must account for.
3. **CI audits and gated actions** (later spec): `edge_audit.py`, the `bootstrap_applied` merge gate under OIDC, and `task secrets:push` as a gate's child command.

The core in this spec must already support what 2 and 3 need: runs without credentials, runs in CI, and JSON/JUnit output.

## Decisions

These are the operator's calls, made while designing.

- **Preflight never mutates.** A gate proves the state before an action, runs the action as a child command, then proves the state after and names the next step. Actions stay as small scripts in the consuming repo.
- **Guidance is a dependency worklist.** Checks declare prerequisites. A check whose prerequisite is not ok is reported as blocked and is not run, so one root cause produces one failure. Blocked checks still fail the gate.
- **Contracts are TOML with references.** Contracts live in the consuming repo. A value is a literal or a reference into a repo file (tfvars, yaml, json) or into SSM, so values already recorded elsewhere are not duplicated.
- **Preflight has its own small runner, not pytest.** testinfra (including `host.ansible`) and boto3 are observation libraries inside checks. The pytest subprocess, its hardening and pytest-html are dropped.
- **Gates declare their order,** and `preflight status` walks them in that order.

## Architecture

### The library (`src/preflight/`)

| Unit | Purpose | Depends on |
|---|---|---|
| `check.py` | The `Check` definition: id, section model, intrinsic prerequisites, `observe(ctx, section) -> Outcome`. The `Outcome` and `NextStep` types, and the `@check` decorator | pydantic |
| `contract.py` | Loads TOML, resolves references, validates each bound section against its check's model, collects every problem before reporting | `resolvers.py`, pydantic |
| `resolvers.py` | `tfvars` (python-hcl2), `yaml`, `json`, and lazy `ssm` | python-hcl2, PyYAML, `context.py` for `ssm` |
| `runner.py` | Orders a gate's check instances by prerequisite, runs them, propagates `blocked`, enforces per-check timeouts, turns exceptions into `error` | `check.py`, `context.py` |
| `context.py` | `ctx.root`, `ctx.host` (testinfra local host), `ctx.aws(identity)` (identity-verified boto3, carried over from today's `gate/aws.py`), `ctx.env(identity)` (subprocess environment with that identity's `AWS_PROFILE`), `ctx.ssm(identity, name)` | pytest-testinfra, boto3 |
| `gate.py` | `Gate(name, before, run, after, after_gates)` and its CLI | `runner.py`, `contract.py`, `render.py` |
| `render.py` | Terminal worklist, JSON, JUnit XML | `check.py` |
| `__main__.py` | `python -m preflight status` and `python -m preflight validate` | `gate.py` |
| `catalog/` | Generic, reusable checks (see "Catalog v1") | the above |

### The consumer (`iac/preflight/`)

```
iac/preflight/
  pyproject.toml        # depends on preflight @ git tag; path override for local work
  contracts/dev.toml
  contracts/prod.toml
  gates/identifiers.py
  gates/github.py
  gates/bootstrap.py
  gates/secrets.py
  gates/pipeline.py
  gates/edge.py
  checks/               # checks that only make sense in iac; same Check API
```

The consumer's directory holds contracts, gates and repo-local checks only; no engine code is copied.

## Contracts

```toml
schema_version = 1
environment = "dev"

[identities.admin]
profile    = "sandbox"
account_id = { tfvars = "envs/dev.tfvars", key = "account_id" }
role       = "AWSReservedSSO_AdministratorAccess_*"

[delegation]
identity = "admin"
root     = { tfvars = "stacks/bootstrap/envs/dev.tfvars", key = "root_domain" }
zones    = { tfvars = "stacks/bootstrap/envs/dev.tfvars", key = "zones" }
```

- **Identities** carry over from today's model with one change: `role` is a role *name* that may contain a `*` glob, because IAM Identity Center role names carry a random suffix. The effective STS caller must be an assumed role in `account_id` whose role name matches. IAM users and root are still refused.
- **Sections** have names the gate author chooses. A gate binds a check instance to a section (`dns.delegated("delegation")`), and the check's pydantic model validates that section. A section no gate binds is an error, as is a bound section that is missing.
- **References** resolve relative to the consumer's repo root, which is the git top-level of the directory holding the contract file.
  - `{ tfvars = "<path>", key = "<name>" }` — a top-level variable in an HCL tfvars file.
  - `{ yaml = "<path>", path = "<dotted.path>" }` and `{ json = "<path>", path = "<dotted.path>" }`.
  - `{ ssm = "<name>", identity = "<alias>" }` — resolved lazily, when the check that uses it runs, under that identity. It is for expected values that are outputs of an earlier stage, such as `/platform/dns/name_servers`. A failed SSM read is that check's `error`, not a contract error.
- **An invalid contract is different from unfinished work.**
  - **Invalid (exit 2, nothing observed):** a wrong shape or type, a missing referenced file or key, an unknown identity, an unbound or missing section, a prerequisite cycle. Every problem is listed in one report.
  - **Unfinished (a failed check with a next step):** a value that resolves but is still a placeholder. Section models mark placeholder values per field (`Field(placeholder="000000000000")`). For each placeholder, the runner inserts a synthetic `contract.placeholder[<section>.<field>]` check that fails with "fill `<key>` in `<file>`", and it becomes a prerequisite of the check bound to that section. The operator's to-dos stay in the worklist.

## Checks

```python
class Delegation(Section):
    identity: IdentityRef
    root: Domain
    zones: list[Label]

@check("dns.delegated", section=Delegation, requires=[aws.session_for("identity")])
def delegated(ctx, s: Delegation) -> Outcome:
    ...
    return fail(
        observed=observed,
        do=f"In Administration, set the NS record for app.{s.root} to:",
        paste=ns_block(...),
        wait="up to 15 minutes before resolvers see it",
        ref="README, DNS step 1",
    )
```

- A catalog check is a factory; calling it with a section name makes an **instance**, whose id is `<check id>[<section>]`, for example `dns.delegated[delegation]`. The exception is `aws.session`, which takes an identity alias rather than a section (`aws.session("admin")`, id `aws.session[admin]`); `aws.session_for("<field>")` is the prerequisite form that reads the alias from a field of the requiring check's section.
- A check declares its **intrinsic prerequisites**; for example, anything that reads AWS requires `aws.session` for the identity its section names. A gate can add prerequisites, including instances from other gates by id.
- `observe` returns an `Outcome` built with `ok`, `fail`, `pending` or `error`. Only the runner produces `blocked`.
- `NextStep` fields: `do` (one imperative sentence), `paste` (optional text ready to copy: a record, a command), `wait` (optional: how long to expect before a recheck), `ref` (optional pointer into the consumer's documentation). All are rendered from contract and observed values, never from secrets.

### Outcomes

| Status | Meaning | Blocks the gate | Worklist shows |
|---|---|---|---|
| `ok` | Observed as expected | no | ✓ |
| `fail` | Observed, and wrong | yes | `do`, `paste`, `ref` |
| `pending` | The operator's part is done; the system is settling (a certificate in `PENDING_VALIDATION`, DNS caching) | yes | `wait`, and when to recheck |
| `error` | Could not observe: expired session, access denied, missing tool, timeout, exception | yes | how to restore observation (`aws sso login --profile sandbox`, install `tofu`) |
| `blocked` | A prerequisite is not `ok`; not run | yes | "waits on: `<id>`" |

- An instance marked `advisory=True` reports `fail`, `pending` and `error` as warnings and never blocks. Its dependents still see it as not ok.
- The worklist marks as **NEXT** the first `fail` or `error` in prerequisite order (ties broken by declaration order). With none, but with a `pending`, it says what is being waited on.

### Error handling and safety

- An exception or timeout inside `observe` is `error`, with the exception type and message. It is never a pass.
- The default timeout is 60 seconds per check; a check or a gate can lower or raise it. Subprocesses run through `ctx.host` with their own timeout.
- `ctx.env(identity)` sets `AWS_PROFILE` and the region for subprocesses, so `tofu` and the `aws` CLI observe as the same identity boto3 verified. The runner does not otherwise alter the environment.
- **The catalog never reads secret values.** Secret checks use metadata (`secretsmanager:DescribeSecret`, `gh secret list`, file existence). An outcome carries only what the check puts in it; command output is never attached by default.

## Gates

```python
# iac/preflight/gates/bootstrap.py
from preflight import Gate
from preflight.catalog import aws, tofu, dns, acm, ssm

gate = Gate(
    "bootstrap",
    after_gates=["identifiers"],
    before=[aws.session("admin")],
    run=["scripts/bootstrap.sh", "{environment}"],
    after=[
        tofu.plan_clean("bootstrap_stack"),
        ssm.parameters("bootstrap_params"),
        dns.delegated("delegation"),
        dns.undelegated("retired_zones"),
        acm.issued("root_certificate"),
    ],
)

if __name__ == "__main__":
    raise SystemExit(gate.main())
```

### A gate run

1. Load the contract, resolve references, validate every bound section, check the graph for cycles and unknown prerequisites. Any problem: print all of them, exit 2.
2. Run the **before** instances in prerequisite order and print the worklist. If any blocking instance is not `ok`, exit 1 without starting the child.
3. Run the **child** command: argv, no shell, the caller's environment, with the consumer's repo root as its working directory, so relative paths such as `scripts/bootstrap.sh` mean the same thing wherever the gate is started. `{environment}` and other `{contract.field}` placeholders in argv are filled from the contract's top-level fields. A child that cannot start exits 127.
4. Run the **after** instances **even when the child failed**, and print the worklist. This is the mid-CI case: `tofu apply` times out validating a certificate, and the after-checks name the missing NS record.
5. Exit with the child's status if it was nonzero; otherwise 0 if every blocking after-instance is `ok`, else 1.

A gate with no `run` has only after-checks; `before` is optional.

### CLI

`python gates/<name>.py --contract contracts/<env>.toml [--dry-run] [--json PATH] [--junit PATH] [--timeout SECONDS] [-- extra child args]`

- `--dry-run` skips step 3, so before and after both run as observations.
- `--json` and `--junit` write the same outcomes in machine-readable form.
- Exit codes: 0 pass, 1 blocked, 2 invalid invocation or contract, or the child's status.

`python -m preflight status --contract contracts/dev.toml [--gates preflight/gates]` imports every gate module in the directory, orders them by `after_gates`, and dry-runs each. It prints one line per gate (satisfied, next step, or waiting on an earlier gate), then the single overall NEXT. A gate whose `after_gates` are not satisfied is reported as waiting and is not run.

`python -m preflight validate --contract contracts/<env>.toml [--gates ...]` performs step 1 for every gate and observes nothing. It needs no credentials.

## Catalog v1

| Check | Observes through | Notes |
|---|---|---|
| `aws.session` | boto3 STS | The effective caller matches the identity. `error` names `aws sso login --profile <profile>` |
| `github.auth` | `gh auth status` | |
| `github.repo_setting`, `github.variables`, `github.environment`, `github.ruleset_requires`, `github.secret_present`, `github.workflow_green` | `gh api` | Secrets by name only |
| `tofu.plan_clean` | `tofu init` + `tofu plan -detailed-exitcode` | 0 ok; 2 `fail` ("rerun the apply"); 1 `error`. The section names the directory and var files |
| `ssm.parameters` | boto3 SSM | Present and non-empty; values are not compared unless the section gives them |
| `dns.delegated`, `dns.undelegated` | stdlib DNS-over-HTTPS | Queries the parent zone's authoritative servers (Cloudflare `application/dns-json`, falling back to Google `/resolve`), with an injectable transport. It implements what the delegated-DNS spec calls `scripts/dns_check.py` |
| `dns.cname` | stdlib DNS-over-HTTPS | Reads `Answer` records, never the response `Status` |
| `acm.issued` | boto3 ACM | `ISSUED` ok; `PENDING_VALIDATION` `pending`, with the CNAME to add; failed or timed out `fail`, with the reissue command from the section |
| `files.present`, `files.git_ignored`, `files.committed` | `ctx.host.file`, `git` | Existence and git status only; never contents of a secret file |
| `sops.rule` | `.sops.yaml` | A creation rule exists for the path |

## The iac bootstrap gates

| Gate | Before | Runs | After | Replaces readiness checks |
|---|---|---|---|---|
| `identifiers` | — | — | account and GitHub IDs filled (placeholders), accounts distinct, values committed | `ids.*` |
| `github` | `github.auth` | — | repo, Actions access set to organization, organization variables, `platform-<env>` environments, `main` ruleset requiring `plans`, `v*` tag ruleset | `gh.repo`, `gh.actions_access`, `gh.variables`, `gh.environment.*`, `gh.ruleset.*`, `gh.auth` |
| `bootstrap` | `aws.session(admin)`; after gate `identifiers` | `scripts/bootstrap.sh {environment}` | `tofu.plan_clean(stacks/bootstrap)`, bootstrap SSM parameters including `/platform/dns/*`, `dns.delegated` per zone, `dns.undelegated` per retired zone, `acm.issued` for the root certificate | `{p}.identity`, `{p}.bootstrap`, `local.bootstrap_state`, the delegated-DNS spec's DNS checks |
| `secrets` | `aws.session(admin)` | none until sub-project 3 | route consistent (committed vs git-ignored), `.sops.yaml` rule, local age identity file present, `SOPS_AGE_KEY` present on route (a), pushed secrets present and tagged `ManagedBy = secrets-push` | `gh.sops_key.*`, the secrets checks |
| `pipeline` | after gates `bootstrap`, `github`, `secrets` | — | last `platform.yml` run on `main` succeeded, `/platform/*` parameters present, alarm subscription confirmed | `gh.merged`, `gh.platform_run`, `{p}.platform`, `{p}.alarms` |
| `edge` | after gate `pipeline` | — | plan-reads bootstrap rerun current (Edge step 1), `/platform/edge/*` present, `edge.entra.<env>` recorded per app that signs in, aliases (advisory unless canonical), reserved labels clean (advisory) | `{p}.plan_reads`, `{p}.edge`, `{p}.entra.*`, the alias and reserved-label checks |

Every gate except `github` runs per environment, selected by the contract. `github` is repository-wide; both contracts bind it, and its checks read the same values.

**Dropped because OpenTofu or the pipeline already proves them:** `{p}.oidc`, `{p}.platform_roles`, `{p}.state_encryption`, `{p}.cluster`, `{p}.region`, `{p}.registry.*`, `{p}.registry_policy`, `{p}.replication`. A clean plan or a green pipeline run stands in for them. `{p}.zone_snapshot` is dropped because the delegated-DNS spec retires it.

**Dropped as one-time acceptance proofs:** `smoke.digest`, `spec.answers`, `gh.tag`, `gh.drift`, `gh.guardrails`, `{p}.smoke`. They stay as steps in `iac`'s platform plan (Task 15).

## Migration in iac

This happens after the library's first tag, in one iac pull request:

- Delete the vendored `iac/preflight/` and replace it with the consumer layout above.
- Add `python -m preflight validate` for both contracts to `task check`.
- Amend the delegated-DNS spec and plan (not started as of 2026-09-29): `dns_check.py` and the readiness changes land as preflight catalog checks and iac gates instead of in `scripts/`, with a dated line in the spec's amendment block.
- Point README, AGENTS.md and the `bootstrap-environment` skill at `preflight status`.
- Delete `scripts/bootstrap_readiness.py` and `scripts/tests/test_bootstrap_readiness.py` only after the acceptance run below.
- `scripts/bootstrap.sh` stays; the `bootstrap` gate runs it.

## Packaging

- `src/` layout, `hatchling`, Python ≥3.12.
- Dependencies: pydantic, boto3, pytest-testinfra, python-hcl2, PyYAML. Optional extra `ansible` for `host.ansible`.
- Consumers pin a `vX.Y.Z` tag of `github.com/strider4560/preflight`; `[tool.uv.sources]` with a path points at a local checkout while both repos change together.
- **Public API:** check ids, section models, `Gate`, the CLI and exit codes, and the JSON output's shape. Renaming a check or tightening a section model is a breaking change and a major version.
- This repo's current `gate/`, `checks/`, `contracts/`, `run.py`, `examples/`, `tests/` and `reports/` are removed. The README is rewritten around the new model. Useful pieces are ported, not kept: the identity verification in `gate/aws.py` and the SDK timeout and retry settings.

## Testing

All offline: no AWS, no network, no credentials.

- **Runner:** prerequisite ordering, `blocked` propagation, advisory instances, cycle and unknown-prerequisite rejection, per-check timeouts, exceptions becoming `error`.
- **Contracts:** each resolver against fixture files; lazy `ssm`; placeholders becoming synthetic failing checks; all problems reported together.
- **Gates:** exit codes; argv without a shell; after-checks running when the child fails (children are `python -c` one-liners); `--dry-run`; `status` ordering and waiting.
- **Catalog:** each check against fakes — a recording fake host, botocore `Stubber`, a fake DNS-over-HTTPS transport, a fake `gh`. Every `fail`, `pending` and `error` test asserts on the rendered next step, because that text is what the operator acts on.
- **Rendering:** golden-file tests of the worklist, JSON and JUnit.
- `ruff check` and `ruff format --check` as today.

## Acceptance

1. The library's tests and lint pass.
2. In iac, `python -m preflight validate` passes for `dev.toml` and `prod.toml`.
3. On one day, with the operator's credentials, `preflight status` runs against Sandbox and Production beside `bootstrap_readiness.py`. Every step the readiness script names appears in the worklist, or is on one of the two drop lists above. Only then is the readiness script deleted.
4. The `bootstrap` gate's after-checks, run with the delegation removed or not yet added, report `fail` for `dns.delegated` with the exact NS block, and `blocked` or `pending` for `acm.issued`.

## Open items for later specs

- **CI access to this repo.** `tellabsadmin/iac`'s workflows can install preflight only if `strider4560/preflight` is public or reachable with a deploy key. Sub-project 3 decides.
- **The age identity check.** iac's AGENTS.md forbids agents from reading an age identity. `secrets` checks only that the file exists. Checking that its public key is a recipient needs `age-keygen -y`, which reads the private key locally; that is left out until the operator decides whether an operator-run tool may do so.
- **Static validation and CI audits:** sub-projects 2 and 3.
