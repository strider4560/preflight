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

## Requirements

Python 3.12 or newer, provided by `uv` / `uvx`. Checks shell out to these tools, and each is
needed only by the checks that use it:

- `aws` CLI: `aws.region`
- `tofu`: `tofu.plan_clean`
- `gh` 2.48 or newer: `github.*` (list endpoints use `gh api --paginate --slurp`)
- `git`: `git.*` and `files.git_ignored` / `files.committed`; contracts must live in a git work tree

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
| `dns.delegated`, `dns.undelegated` | The parent zone's own servers delegate exactly the expected servers, or nothing. Answers come only from the parent zone's own servers; a lookup nobody answered is an error, never ok |
| `dns.cname`, `dns.caa` | Records the operator adds by hand |
| `github.auth`, `repo`, `variables`, `environments`, `ruleset`, `secret_names`, `workflow_green` | GitHub settings; secrets remain name-only |
| `git.up_to_date` | The checkout contains the remote branch's latest commit (no fetch) |
| `files.present`, `absent`, `git_ignored`, `committed` | Files and their git status |
| `sops.rule` | A creation rule with enough age recipients covers each secrets file |

## Commands and exit codes

| Command | Exit |
|---|---|
| `preflight check <gate.py> --contract <file> [--json F] [--junit F] [--jobs N]` | 0 every blocking item ok; 1 not; 2 invalid contract or gate (nothing observed); 3 preflight bug; 130 interrupted |
| `preflight status [--gates D] [--contracts D] [--json F] [--jobs N]` | 0 only when every milestone gate is satisfied everywhere; 2 for a consumer whose environment gates have no environment contract |
| `preflight validate [--gates D] [--contracts D]` | 0 or 2 (also 2 for a consumer whose environment gates have no environment contract); needs no credentials; add it to CI |

An internal error (exit 3) prints only the exception type.

Each instance runs in its own worker process with a cleaned environment: stray AWS credential
variables and `TF_CLI_ARGS*`, `TF_WORKSPACE`, `TF_VAR_*` are removed, as are AWS endpoint-override and
container-credential variables (except for `aws.assumed`, which observes the caller's own
shell), and the identity's profile and region are set. Workers run Python in safe-path mode,
so files in the consumer's repository cannot shadow preflight's imports. Timeouts kill the worker's whole process group. JSON reports are
written with mode 0600; JUnit never marks anything skipped.

## Developing preflight

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

The design is `docs/superpowers/specs/2026-09-29-preflight-library-design.md`.
