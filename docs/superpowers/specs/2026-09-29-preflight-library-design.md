# Preflight as an importable library — design

| | |
|---|---|
| Date | 2026-09-29 (revised the same day after four adversarial reviews) |
| Status | Draft, awaiting operator review |
| Design driver | `tellabsadmin/iac` (`~/Develop/tellabs/iac`), its account bootstrap |
| Replaces | This repo's vendored drop-in scaffold (`gate/`, `checks/`, `contracts/`, `run.py`) |

## Why

The operator juggles several projects. Weeks away from one, they no longer remember what it needs from them, and resuming becomes dangerous or tedious. In `iac` the failures cluster around work only the operator can do: DNS records in the Administration account, secrets and their age identities, IAM and GitHub settings, and cross-account dependencies that must settle between pipeline runs. The standard example: a delegated zone such as `app.tellabs.dev` lives in Sandbox while its root lives in Administration, managed by hand. If the operator merges before adding the NS record, the edge stack's apply fails mid-CI on certificate validation, and the operator adds the record and reruns, knowing the second run will pass.

Preflight turns that loop into a guardrail with two jobs:

1. **Validate** the things *the operator* is responsible for, so the automations can proceed unhindered. It does not re-prove what OpenTofu or the pipeline already prove; sometimes a clean `tofu plan` is the whole check.
2. **Guide forward.** Every failure names the next step, with text ready to paste, and one command answers "where was I?".

## Principles

These are the operator's calls, made while designing and after review.

- **Preflight never runs what it gates.** A consumer's script calls preflight first and refuses to continue unless it exits 0:

  ```bash
  # top of iac/scripts/bootstrap.sh
  preflight() { uvx --from "git+https://github.com/strider4560/preflight@v0.1.0" preflight "$@"; }
  preflight check preflight/gates/bootstrap_entry.py --contract "preflight/contracts/$env.toml" || exit 1
  ```

  A script may also call a gate at its end, as guidance rather than as a condition (`|| true`), to show the operator what is left to do.
- **Preflight never mutates** the things it observes.
- **testinfra is the core observation layer.** Probes go through testinfra, and through Ansible modules via testinfra's Ansible backend, so the operator does not have to track how vendors' commands and APIs change or how to parse their responses. Only where no module can observe the fact does a check use a library directly (DNS referrals, below).
- **Guidance is a dependency worklist.** Checks declare prerequisites. A check whose prerequisite is not ok is reported as blocked and not run, so one root cause produces one failure.
- **Contracts are TOML files with references** into the consumer's own files, so values already recorded there are not duplicated.
- **v1 stays minimal.** It does not anticipate CI identities (OIDC), GitHub identity kinds, or multi-repository checks; supporting them later may take a major version.

## Scope and decomposition

This spec covers the library and catalog v1, including every `dns.*` check, designed so that iac's bootstrap can be expressed with it. The iac gate table below is the design driver and a test of the catalog's expressiveness; **nothing in the iac repository changes under this spec.** The delegated-DNS rollout proceeds on its own, with its own `scripts/dns_check.py`.

Later sub-projects, each with its own spec:

1. **iac migration:** iac's contracts, gates and repo-local checks; `bootstrap_readiness.py` retired after a side-by-side run; `dns_check.py` and the bootstrap guard moved onto preflight.
2. **Static repository validation:** `registry_check.py`, `env_secrets.py check`, `pin_actions.py --check`, `onboard_check.py`.
3. **CI audits:** `edge_audit.py` and the `bootstrap_applied` merge gate running in GitHub Actions.

## Concepts

- A **check** is a reusable probe: an id (`dns.delegated`), a pydantic **section model**, intrinsic prerequisites, and an `observe` function.
- A **contract** is a TOML file in the consumer repo holding expected values, as literals or references.
- A **section** is a named table in a contract. A **check instance** is a check bound to a section: `dns.delegated("delegation")`, id `dns.delegated[delegation]`.
- A **gate** is a Python file in the consumer repo that names a set of check instances, and optionally other gates it `requires`.
- An **outcome** is what one observation produced; a check may produce several **items** (one per zone, per app, per alias).

## Architecture

### The library (`src/preflight/`)

| Unit | Purpose |
|---|---|
| `check.py` | `Check`, the `@check` decorator, `Section` base model, `Outcome`, `Item`, `NextStep` |
| `contract.py` | Load TOML, detect references, detect placeholders, resolve references, validate sections; report every problem at once |
| `resolvers.py` | `tfvars`, `yaml`, `yaml_glob`, `json`, and lazy `ssm` |
| `identity.py` | Identity specs, role matching, the scrubbed per-identity environment |
| `worker.py` | Runs one check instance in its own process: environment, working directory, testinfra hosts, result marshalling |
| `runner.py` | Builds the instance graph, runs instances in dependency order (bounded parallelism), enforces timeouts, propagates `blocked` |
| `gate.py` | `Gate`, gate loading by file path, `requires` resolution |
| `render.py` | Terminal worklist, JSON, JUnit |
| `cli.py` | `preflight check`, `preflight status`, `preflight validate` |
| `catalog/` | Catalog v1 (below) |

### The consumer

```
<repo>/preflight/
  contracts/dev.toml
  contracts/prod.toml
  contracts/repo.toml      # optional: gates with scope "repository"
  gates/<name>.py
  checks/<name>.py         # repo-local checks, same Check API
```

The consumer needs no Python project of its own: `uvx` provides preflight and its dependencies, and preflight loads the consumer's gate and check files by path.

- Gate files are loaded with `importlib.util.spec_from_file_location` under the package name `consumer.gates.<file stem>`. The consumer directory (the parent of `gates/`) is registered as the package `consumer`, so a gate imports a repo-local check as `from consumer.checks.secrets import route_consistent`. Nothing is inserted into `sys.path`, so a gate named `secrets.py` or `github.py` cannot shadow another module.
- Gate and check files are trusted code: loading them executes them.

## Contracts

```toml
schema_version = 1
scope = "environment"          # or "repository"
environment = "dev"            # required when scope = "environment"

[identities.admin]
profile        = "sandbox"
region         = "us-east-1"
account_id     = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID from the AWS access portal" }
permission_set = "AWSAdministratorAccess"

[delegation]
identity     = "admin"
root         = { tfvars = "envs/dev.tfvars", key = "root_domain" }
zones        = { tfvars = "envs/dev.tfvars", key = "zones" }
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json", how = "rerun scripts/bootstrap.sh dev from an up-to-date main" }

[delegation.remedy]
ref = "README, DNS step 1"
```

### Top level

`schema_version` (1), `scope`, and `environment` when the scope is `environment`. Any other top-level key is an error. `environment` is the only field available to templates as `{environment}`.

### Identities

- `profile` names an AWS profile. `region` is required; a section may override it with its own `region` field.
- `account_id` is required.
- Exactly one of `role` (an exact IAM role name) or `permission_set` (an IAM Identity Center permission set). A permission set matches the role name `AWSReservedSSO_<permission_set>_<16 hex digits>` by full match. The caller must be an assumed role in `account_id`; IAM users and root are refused.
- An identity is verified in one of two ways, as two different checks (see Catalog): **explicitly**, with the named profile, for preflight's own observations; or **ambiently**, with the caller's environment exactly as it is, for entry gates guarding a script that will use ambient credentials.

### References

A table value is a reference when it contains exactly one of the source keys `tfvars`, `yaml`, `yaml_glob`, `json` or `ssm`; otherwise it is an ordinary table. References may appear anywhere a value may, including inside arrays. Paths resolve relative to the consumer's repo root: the git top-level of the directory holding the contract. A contract outside a git work tree is an error.

| Source | Keys | Result |
|---|---|---|
| `tfvars` | `key` | A top-level variable, parsed with python-hcl2 8.x using `SerializationOptions(strip_string_quotes=True, preserve_heredocs=False)`. Only literals, lists and maps are accepted; a value containing `${` or an HCL function call is an error |
| `yaml`, `json` | `path` (optional) | The document, or the value at a dotted path; a path segment that is an integer indexes a list |
| `yaml_glob` | `path` (optional) | A list of `{file, value}` for every matching file, sorted by file name |
| `ssm` | `identity`, `format` (`text` or `json`), `path` (optional, into the JSON) | Resolved lazily (below) |

Every reference may also carry:

- `placeholder`: a list of values meaning "not filled in yet";
- `how`: one sentence on where the value comes from, or a command that prints it (for example `gh api orgs/tellabsadmin --jq .id`).

### Placeholders

Placeholder detection runs on resolved values **before** type validation. It compares against the reference's own `placeholder` list; the library knows no placeholder values of its own. This lets a consumer leave a deliberate `000000000000` unmarked where it is intentional.

For each placeholder found, the runner adds a synthetic instance `contract.placeholder[<dotted path>]`, for example `contract.placeholder[identities.admin.account_id]` or `contract.placeholder[delegation.zones[0]]`. It fails with "fill `<key>` in `<file>`" plus the reference's `how`. It becomes a prerequisite of every instance bound to the section holding it; a placeholder in an identity becomes a prerequisite of that identity's session checks, and so of everything that uses the identity. Type validation of that field is skipped until the value is filled.

### Lazy SSM references

An `ssm` reference is resolved when the instance that uses it runs, never while loading the contract.

- It may appear only in a section field, never in an identity, never at the top level, and never in a section's `identity` field.
- It adds two implicit prerequisites: `aws.session[<identity>]`, and `ssm.present[<parameter name>]`. A missing parameter therefore fails once, as `ssm.present[...]`, with the reference's `how` as its next step, and blocks the dependents instead of producing one `error` each.
- Parameters are read with decryption off. A `SecureString` parameter is an `error` for `ssm.present`: an expected value must never be secret.
- After resolution the field is type-validated; a failure is an `error` of the dependent instance, naming the parameter.

### Sections

- A section's name is chosen by the consumer. A check instance binds to one section, and several instances may bind the same section.
- A section is valid when it validates against the model of every check bound to it. Unknown keys are allowed by each individual model, but a key that no bound model declares is an error. That rule is enforced only by `validate` and `status`, which load every gate; `check` enforces it only for the gate it runs.
- Every section may carry a `[<section>.remedy]` table: `do`, `paste`, `wait` and `ref` strings that override or extend the check's own next step, templated from section fields and `{environment}`. This is how a generic check gets consumer-specific guidance, such as which script reruns bootstrap and from which branch.

### Validation errors

Each of these makes the contract or gate invalid (exit 2, nothing observed), and every one found is listed together:

- a missing file or key, or a malformed reference;
- a wrong shape or type;
- an unknown identity;
- a section missing for a bound instance;
- an unknown gate in `requires`;
- a prerequisite cycle;
- a gate with no instances.

A value that resolves but is a placeholder is not an error; it is a failing synthetic check.

## Checks

```python
class Delegation(Section):
    identity: IdentityRef
    root: Domain
    zones: list[Label]
    name_servers: dict[Label, list[Domain]]

@check("dns.delegated", section=Delegation, requires=[aws.session_for("identity")])
def delegated(ctx, s: Delegation) -> Outcome:
    items = []
    for prefix in s.zones:
        expected = s.name_servers.get(prefix)
        if expected is None:
            items.append(fail(prefix, do=f"{prefix} has no zone yet", use_remedy=True))
            continue
        observed = ctx.dns.parent_ns(f"{prefix}.{s.root}")
        if norm(observed) == norm(expected):
            items.append(ok(prefix, observed=observed))
        else:
            items.append(fail(prefix, observed=observed,
                do=f"In Administration, set NS {prefix}.{s.root} to exactly these servers, replacing any others:",
                paste=ns_block(prefix, s.root, expected)))
    return Outcome(items)
```

- An `Outcome` holds one or more items. A check that observes one thing returns a single unnamed item. Item ids extend the instance id: `dns.delegated[delegation]:app`.
- A check declares **intrinsic prerequisites**. `aws.session_for("<field>")` means "the session check for the identity named in that section field". A gate cannot add prerequisites between individual instances; it orders whole gates with `requires`.
- An item's status is `ok`, `fail`, `pending` or `error`; only the runner produces `blocked`. The instance's status is the worst of its items' blocking statuses.
- A check marks an item `advisory` from its own logic, for example an alias CNAME that is not canonical. An advisory item never blocks the gate, and it does not block the instance's dependents.
- `NextStep` fields: `do` (one imperative sentence), `paste` (optional text to copy: a record, a command), `wait` (optional: how long to expect before a recheck), `ref` (optional pointer into the consumer's documentation). The section's `remedy` table is merged over them. Next steps are rendered from contract and observed values, never from secrets.
- `observe` returning anything other than an `Outcome` is an `error`.

### Outcomes

| Status | Meaning | Blocks the gate | Worklist shows |
|---|---|---|---|
| `ok` | Observed as expected | no | ✓ |
| `fail` | Observed, and the operator has something to do | yes | `do`, `paste`, `ref` |
| `pending` | The operator's part is visibly done and the system is settling | yes | `wait`: what is being waited on and when to recheck |
| `error` | Could not observe: expired session, access denied, missing tool, timeout, exception | yes | how to restore observation |
| `blocked` | A prerequisite is not ok; not run | yes | "waits on: `<id>`" |

`pending` is used only when the operator's step can be seen to be done. For example, a certificate in `PENDING_VALIDATION` is `fail` while its validation CNAME is absent, and `pending` only once the CNAME resolves.

### The worklist

- The **open steps** are every `fail` and `error` item, in dependency order (ties broken by the order instances are declared in the gate), then every `pending` item. Steps with identical rendered `do` and `paste` are merged.
- **NEXT** is the first open step.
- Advisory items are listed separately as warnings.
- `blocked` instances are listed under the step they wait on.

## Execution

### Workers

Each check instance runs in its own worker process, started in a new session (its own process group).

- **Working directory:** the consumer's repo root.
- **Environment:** the caller's environment, minus:
  - AWS credential variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_SECURITY_TOKEN`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME`, `AWS_WEB_IDENTITY_TOKEN_FILE`;
  - OpenTofu overrides: `TF_CLI_ARGS`, `TF_CLI_ARGS_*`, `TF_WORKSPACE`, `TF_VAR_*`;
  
  plus `AWS_PROFILE`, `AWS_REGION` and `AWS_DEFAULT_REGION` for the instance's identity when it has one. The directory of preflight's own executables is prepended to `PATH`, so the `ansible` command from the same environment is found. `aws.assumed` alone runs with the caller's AWS variables intact.
- **Hosts:** the context gives two testinfra hosts:
  - `ctx.host` = `testinfra.get_host("local://")`, for `host.run`, `host.file`, `host.exists`;
  - `ctx.ansible` = `testinfra.get_host("ansible://localhost")` with a generated inventory: `ansible_connection=local` and `ansible_python_interpreter=<preflight's Python>`, so modules find boto3. AWS modules always receive the identity's `profile` and `region` explicitly as module arguments.
- **Result:** the worker returns only a serialized `Outcome`. Exceptions are reduced to their type, plus the Ansible module name or AWS error code when there is one; exception messages and command output never leave the worker unless a check puts a specific, safe value into an item.
- **Timeouts:** the default is 60 seconds per instance, set per check (`tofu.plan_clean` defaults to 600) and overridable per section (`timeout`). On expiry the runner kills the worker's whole process group, which ends any `ansible` or `tofu` processes testinfra started, and records `error` ("timed out after N s").
- **Parallelism:** at most four workers at once (`--jobs`).
- **Ctrl-C:** the whole run is stopped, every worker process group is killed, no outcome is reported as ok, and preflight exits 130.

### Gates

```python
# <repo>/preflight/gates/bootstrap_entry.py
from preflight import Gate
from preflight.catalog import aws, git, files

gate = Gate(
    "bootstrap_entry",
    guards="scripts/bootstrap.sh",
    requires=["identifiers"],
    checks=[aws.assumed("admin"), aws.region("admin"), git.up_to_date("branch")],
)
```

- `requires` names other gates, by file stem, in the same `gates/` directory. When `preflight check` runs a gate, the required gates' instances run too, as prerequisites of this gate's instances, so a script calling one gate cannot proceed while an earlier gate is unfinished. In `status` it gives the order.
- `guards` marks an entry gate: one that exists to be called by the named script. `status` leaves entry gates out of the "where was I?" view, because their checks describe the moment before an action, not a milestone. The next step for an unfinished milestone comes from its own checks' `remedy`, for example "run `scripts/bootstrap.sh dev` from an up-to-date main".
- `scope` comes from the gate: `Gate(..., scope="repository")` gates run against a contract with `scope = "repository"`; the default is `"environment"`. When an environment gate requires a repository gate, `check` loads the repository contract `repo.toml` from the directory holding the environment contract; its absence is an error (exit 2).

### Commands

```
preflight check  <gate.py> --contract <file> [--json PATH] [--junit PATH] [--jobs N]
preflight status [--gates DIR] [--contracts DIR] [--json PATH]
preflight validate [--gates DIR] [--contracts DIR]
```

- **`check`** loads the contract and the gate with everything it requires, validates, runs, prints the worklist, and exits:

  | Exit | Meaning |
  |---|---|
  | 0 | Every blocking item is ok |
  | 1 | At least one blocking item is not ok |
  | 2 | Invalid invocation, contract or gate; nothing was observed |
  | 3 | Preflight itself failed (an internal error) |
  | 130 | Interrupted |

- **`status`** defaults `--gates` to `preflight/gates` and `--contracts` to `preflight/contracts` under the repo root. It runs every milestone gate (every gate without `guards`) against every contract of matching scope. For each environment it prints one line per gate: satisfied, open (with the count of open steps), or waiting on a required gate. Gates waiting on an unsatisfied required gate are still observed; their open steps are listed below the main list as "also open", because some operator steps expire (an SNS confirmation link, a certificate's 72 hours). It ends with the ordered open steps across all environments and one overall NEXT. Exit 0 only when every milestone gate is satisfied in every environment, 1 otherwise, and 2 or 3 as for `check`.
- **`validate`** loads every gate and contract, applies every validation rule including unused section keys, observes nothing, and needs no credentials. Exit 0 or 2.

### Output

- **Terminal:** the worklist, one line per instance, then the open steps with NEXT first.
- **JSON** (`--json PATH`, written atomically with mode 0600):

  ```json
  {
    "schema_version": 1,
    "preflight_version": "0.1.0",
    "command": "check",
    "exit_code": 1,
    "started_at": "…", "finished_at": "…",
    "inputs": [{"path": "preflight/contracts/dev.toml", "sha256": "…"}],
    "runs": [{
      "gate": "delegation", "environment": "dev",
      "instances": [{
        "id": "dns.delegated[delegation]", "check": "dns.delegated", "section": "delegation",
        "gate": "delegation", "status": "fail", "requires": ["aws.session[admin]"],
        "blocked_by": [], "duration_s": 1.4, "error_type": null,
        "items": [{"id": "dns.delegated[delegation]:app", "status": "fail", "advisory": false,
                   "observed": ["…"], "next_step": {"do": "…", "paste": "…", "wait": null, "ref": "…"}}]
      }]
    }],
    "open": ["dns.delegated[delegation]:app"],
    "next": "dns.delegated[delegation]:app"
  }
  ```

  `inputs` lists the contract and every file a reference read, with hashes taken when they were read. If any of them changed by the end of the run, the run exits 1 with the reason "inputs changed during the run".
- **JUnit** (`--junit PATH`): one test case per item. `fail`, `pending` and `blocked` are `<failure>`; `error` is `<error>`; advisory items are passing cases with the warning in `<system-out>`. Nothing is ever `<skipped>`, so a blocked gate can never render green.
- There is no default report directory; files are written only where asked.

## Catalog v1

| Check | Observes through | Semantics |
|---|---|---|
| `aws.session[<identity>]` | `amazon.aws.aws_caller_info` with the identity's `profile` | Account and role match. `error` "run `aws sso login --profile <profile>`" |
| `aws.assumed[<identity>]` | `amazon.aws.aws_caller_info` with no profile, in the caller's unmodified environment | Same match, for what the caller's next command will use. `fail` names the variable to set or unset (`export AWS_PROFILE=sandbox`, `unset AWS_ACCESS_KEY_ID …`) |
| `aws.region[<identity>]` | `host.run("aws configure get region --profile <p>")` | The profile's configured region equals the identity's `region` |
| `ssm.present[<name>]` | the `amazon.aws.aws_ssm` lookup (`decrypt=False`) through `ansible.builtin.debug` | Exists, non-empty, not `SecureString`. Mostly implicit, from lazy references |
| `ssm.parameters` | as above | Every listed name is present |
| `acm.issued` | `amazon.aws.acm_certificate_info` | `ISSUED` ok. `PENDING_VALIDATION` with the validation CNAME absent (checked with `dns.cname` logic): `fail`, pasting the record. CNAME present: `pending`. `FAILED` or `VALIDATION_TIMED_OUT`: `fail`, using the section's `remedy` for the reissue command. The certificate ARN comes from the section (typically a lazy SSM reference) |
| `tofu.plan_clean` | `host.run` | `TF_DATA_DIR` in a fresh temporary directory; `tofu -chdir=<dir> init -input=false -reconfigure -lockfile=readonly` with the section's `var_files` in order; then `plan -lock=false -input=false -no-color -detailed-exitcode`. Exit 0 ok; 2 `fail` with the section's `remedy`; 1 `error` carrying the first `Error:` summary line, truncated to 200 characters. Refuses (`error`) if the directory holds a `backend.tf.off` or a local `terraform.tfstate`, which would make the plan read the wrong state |
| `dns.delegated` | `dnspython` | Per prefix: find the root's authoritative servers, send a non-recursive `NS <prefix>.<root>` query over UDP (TCP on truncation) to them in turn, and read the referral's Authority section. The set must equal the expected servers exactly, case-insensitively, without trailing dots. SERVFAIL, REFUSED or no answer from every server: `error` |
| `dns.undelegated` | `dnspython` | Per prefix: the parent gives no referral (authoritative NXDOMAIN, or NOERROR with its own SOA). A referral: `fail` "remove NS …". Anything else, including a lame delegation: `error`, never ok |
| `dns.cname` | `dnspython`, recursive | Per name: a CNAME to the expected target. Reads the answer records, never only the response code. `fail` notes that a correction may take up to the zone's SOA negative TTL to show; advisory per item when the section says so |
| `dns.caa` | `dnspython`, recursive | The root carries a CAA record allowing the expected issuer |
| `github.auth` | `host.run("gh auth status")` | `error` "run `gh auth login`" |
| `github.repo`, `github.variables`, `github.environments`, `github.ruleset`, `github.secret_names`, `github.workflow_green` | `host.run("gh api … --jq …")` | Settings present; required status checks and enforcement for rulesets; secrets by name only; the last run of a workflow on a branch concluded `success` |
| `git.up_to_date` | `host.run("git …")` | The current branch contains the section's ref (for example `origin/main`) after a fetch; `fail` pastes the rebase or checkout |
| `files.present`, `files.absent`, `files.git_ignored`, `files.committed` | `ctx.host.file`, `git` | Existence and git status only; never the contents of a secret file |
| `sops.rule` | `ctx.host.file` on `.sops.yaml` | A creation rule matches the path, and has at least the section's `min_recipients` |

The catalog never reads secret values.

## The iac bootstrap as the design driver

This table is not built under this spec. It records how iac's bootstrap maps onto the catalog, so the catalog is designed against a real consumer; the iac migration spec will refine it. "Operator" means the manual step the checks observe.

| Gate | Kind | Requires | Checks |
|---|---|---|---|
| `repository` | milestone, repository scope | — | `github.auth`, `github.repo`, Actions access set to organization, organization variables (both account IDs, so repository scope) |
| `identifiers` | milestone, repository scope | `repository` | account and GitHub IDs filled (placeholders, with `how` pasting the `gh api` command), accounts distinct, peer and smoke IDs consistent, values committed (repo-local checks) |
| `bootstrap_entry` | entry, guards `scripts/bootstrap.sh` | `identifiers` | `aws.assumed[admin]`, `aws.region[admin]`, `git.up_to_date` |
| `bootstrap` | milestone | `identifiers` | `tofu.plan_clean(stacks/bootstrap)` with remedy "rerun `scripts/bootstrap.sh {environment}` from an up-to-date main"; `ssm.parameters` for bootstrap's parameters (`/platform/state/*`, `/platform/oidc/provider_arn`, `/platform/dns/zone_ids`, `/platform/dns/name_servers`, `/platform/dns/root_certificate_arn`, `/platform/bootstrap/applied_from`); no leftover local state |
| `delegation` | milestone | `bootstrap` | `dns.delegated` per zone, `dns.undelegated` per retired zone, `acm.issued` for the root certificate, `dns.caa` on the root. `bootstrap.sh` calls this gate at its end as guidance |
| `github` | milestone, repository scope | `bootstrap` | `platform-dev` and `platform-prod` environments; `main` ruleset requiring `validate`, `plans` and later `bootstrap_applied`, with enforcement active; `v*` tag ruleset |
| `secrets` | milestone | `bootstrap` | Repo-local and conditional: ok when no secret is declared and no file is committed; otherwise route consistent, `sops.rule` (two recipients on route (a)), `SOPS_AGE_KEY` by name on route (a), pushed secrets present and tagged. Not required by later gates |
| `pipeline` | milestone | `delegation`, `github` | `github.workflow_green(platform.yml, main)`, `/platform/*` parameters present (zone ID and name among them), alarm subscription confirmed |
| `edge` | milestone | `pipeline` | `/platform/bootstrap/applied_from` matches the commit that last changed `modules/plan-reads`; `/platform/edge/*` present; per app that signs in, `edge.entra.<env>` recorded and `/platform/apps/<name>/edge/client_id` published (repo-local, over `yaml_glob` of `apps/*.yaml`); aliases via `dns.cname` (advisory unless canonical); reserved labels clean (advisory) |
| `drift` | milestone, repository scope | `pipeline` | `github.workflow_green(platform.yml, schedule)`: the weekly drift run's last result, which accumulates while the operator is away |

**Dropped, because OpenTofu or the pipeline proves them:** `{p}.oidc`, `{p}.platform_roles`, `{p}.cluster`, `{p}.registry.*`, `{p}.registry_policy`, `{p}.replication`. `{p}.state_encryption` is dropped too, because `backend.tf` fixes `kms_key_id` and a clean bootstrap plan covers the key; a plan does not read the object's encryption headers. `{p}.zone_snapshot` is retired by the delegated-DNS spec.

**Dropped as one-time acceptance proofs:** `smoke.digest`, `spec.answers`, `gh.tag`, `gh.guardrails`, `{p}.smoke`. They stay in iac's platform plan, Task 15.

**Moved to static validation (sub-project 2):** `ids.registry`.

**Gone with the flags that produced them:** `aws.assessed`, `github.assessed`. `status` always assesses every environment.

## Packaging and distribution

- `src/` layout, `hatchling`, Python ≥3.12.
- Dependencies: `pytest-testinfra`, `ansible` (the full package, which bundles `amazon.aws` and `community.general`, so there is no separate collection install to forget), `boto3` (required by `amazon.aws` in the same interpreter), `dnspython`, `python-hcl2>=8,<9`, `PyYAML`, `pydantic>=2,<3`.
- The repository is public at `github.com/strider4560/preflight`. Consumers run a pinned tag with `uvx --from "git+https://github.com/strider4560/preflight@vX.Y.Z" preflight …`, defined once in the calling script.
- **Public API:** check ids and their section models, `Gate`, the `consumer` import package, the CLI with its exit codes, and the JSON shape. Renaming a check, tightening a section model, or changing an exit code is a major version.
- This repository's current `gate/`, `checks/`, `contracts/`, `run.py`, `examples/`, `tests/` and `reports/` are removed, and the README is rewritten around the new model. The identity matching rules and the SDK timeout and retry settings in `gate/aws.py` are ported where they still apply.

## Risks to retire first

The implementation plan starts with an offline spike, kept only if it passes:

1. `testinfra.get_host("ansible://localhost")` with a generated local inventory runs `amazon.aws.aws_caller_info` and `amazon.aws.acm_certificate_info` from a `uvx`-installed environment, with the `ansible` command found on the prepended `PATH`.
2. `ansible.builtin.debug` with a `lookup('amazon.aws.aws_ssm', …, decrypt=False)` returns a parameter's value through `host.ansible`.
3. Killing a worker's process group ends a running `ansible` child and a running `tofu plan`.
4. python-hcl2 8.x with the serialization options above returns unquoted strings, lists and maps from iac's actual `envs/dev.tfvars`.

If 1 or 2 fails, the fallback for that probe is `host.run` of the `aws` CLI with `--output json`, decided per probe and recorded in the plan.

## Testing

All offline: no AWS, no network, no credentials.

- **Contracts:**
  - each resolver against fixture files, including tfvars quoting, heredocs and interpolation rejection;
  - reference detection;
  - placeholders (identities, list items, `how`, detection before type validation);
  - lazy `ssm` rules and their implicit prerequisites;
  - shared sections and unused keys;
  - every validation error reported together.
- **Runner:**
  - dependency order and `blocked` propagation;
  - advisory items and their dependents;
  - cycles;
  - gate `requires`;
  - timeouts killing a process group (a worker that sleeps in a child process);
  - Ctrl-C;
  - a non-`Outcome` return;
  - an exception reduced to its type.
- **Workers:**
  - environment scrubbing;
  - identity environment;
  - `aws.assumed` keeping the caller's variables;
  - the working directory;
  - output never leaving the worker.
- **Catalog:**
  - every check against fakes: a fake testinfra host that records commands and returns scripted results, a fake Ansible module layer, a fake DNS transport that returns wire-format referrals, NXDOMAIN, SERVFAIL and truncation;
  - every `fail`, `pending` and `error` test asserts on the rendered next step, because that text is what the operator acts on.
- **CLI:** exit codes of all three commands; `status` ordering, entry-gate exclusion and "also open"; input hashes and "inputs changed".
- **Rendering:** golden files for the worklist, JSON and JUnit.
- `ruff check` and `ruff format --check`.

## Acceptance

1. The spike's four questions are answered, and the answers are recorded in the plan.
2. The tests and lint pass.
3. On one day, with the operator's credentials, a scratch contract and scratch gates outside the iac repository run every catalog check once against Sandbox (and `tofu.plan_clean` against iac's `stacks/bootstrap`, read-only). Each check produces a correct `ok`, or a `fail`/`pending` whose next step is accurate for the account's actual state.
4. With a zone whose NS record is absent, `dns.delegated` reports `fail` with the exact NS block, and `acm.issued` for a certificate whose CNAME is absent reports `fail` with the record, independently of the delegation.

## Open items

- **A PyPI name.** `uvx preflight check …`, without `--from`, needs a published package, and the name `preflight` is probably taken. Until one is chosen, consumers use the `--from git+…` form.
- **The age identity check.** iac's AGENTS.md forbids agents from reading an age identity. v1 checks only that the identity file exists. Checking that its public key is a recipient needs `age-keygen -y`, which reads the private key locally; the operator decides later whether an operator-run tool may do so.
