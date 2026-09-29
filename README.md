# IaC preflight

Drop this entire `preflight/` directory into your IaC repository. It is an independent
`uv` project: Python 3.12+, pytest, pytest-testinfra, boto3, and typed TOML contracts.

The runner executes every check in the contract's selected modules, writes reports,
and optionally starts another command **only when every selected check passes**.
The included checks observe existing infrastructure; they do not repair it.

## Start here

Run these commands from your IaC repository root:

```bash
uv sync --project preflight --locked
cp preflight/contracts/prod.example.toml preflight/contracts/prod.local.toml
```

Edit `prod.local.toml`: replace all example account IDs, role ARNs, resource IDs,
profiles, capacity budgets and state settings, then set `example = false`.
The unedited example is deliberately rejected before any AWS calls.

```bash
# Run the required checks and write reports.
uv run --project preflight --locked python preflight/run.py \
  --contract preflight/contracts/prod.local.toml

# Gate an existing operational script. Its working directory remains the repo root.
uv run --project preflight --locked python preflight/run.py \
  --contract preflight/contracts/prod.local.toml \
  -- ./scripts/deploy.sh

# Or gate the included init-and-plan example; this does not apply the plan.
uv run --project preflight --locked python preflight/run.py \
  --contract preflight/contracts/prod.local.toml \
  -- bash preflight/examples/terraform-plan.sh -out=tfplan
```

An existing Python/uv workspace may need `preflight` excluded from its workspace
members if you want this to remain an independent project. Keep `uv.lock` committed.
For CI, use `--locked --no-dev` with both `uv sync` and `uv run`.

## Layout

| Path | Purpose |
| --- | --- |
| `run.py` | Public command runner |
| `gate/cli.py` | Launch pytest, validate completed evidence, then run an optional command |
| `gate/plugin.py` | Mandatory-check semantics and JSON reporting |
| `gate/contract.py` | TOML schema, validation, workspace state-key resolution |
| `gate/aws.py` | Explicit, identity-verified Boto3 sessions and clients |
| `gate/assertions.py` | Optional expected/observed/remediation failure formatting |
| `checks/conftest.py` | Shared fixtures and contract-based parametrization |
| `checks/identity/` | Effective AWS account and role |
| `checks/state/` | Bucket configuration and exact state-object access |
| `checks/network/` | Shared subnet identity, AZ placement and IPv4 capacity |
| `checks/runtime/` | HTTP readiness from existing Testinfra hosts |
| `contracts/` | Environment-specific expectations; no credentials |
| `tests/` | Offline tests of the scaffold itself |

## Contracts and credentials

The contract is the expected state. AWS responses are observations. Do not derive the
expected account, resource ownership, or minimum capacity from those same observations.
Module names select directories under `checks/`; all selected modules are mandatory.
Unselected modules are not imported or run. A missing/empty selected module blocks.

Each entry under `[identities.NAME]` declares an expected account and IAM role ARN.
It can also name a standard AWS profile. Omit `profile` to use Boto3's normal credential
chain, including CI environment credentials or a workload role. Role assumption, SSO,
web identity, source profiles and credential refresh are delegated to the AWS SDK.
No AWS access keys or secret values belong in this contract.

Every `aws.client("NAME", "SERVICE")` validates the effective STS caller before
creating the service client, even if the `identity` module was not selected.
This scaffold deliberately accepts assumed roles only, not IAM users or root.
Role paths are normalized to the role name in the STS assumed-role ARN; account and
partition must also match. Role recreation is not detected by a pinned IAM role ID.

**Terraform authentication remains independently configured.** The runner does not
export Boto3 credentials to Terraform or change `AWS_PROFILE`. Make both tools use
matching profiles and role chains. For example, the example contract corresponds to
an AWS provider using profile `terraform-prod` and an S3 backend using profile
`terraform-state`. The default credential chain only works for both when their
resulting identities agree. Also align provider/backend regions and workspace settings.

When Terraform's provider has an inline `assume_role` configuration, mirror that effective
role through an AWS profile used by Boto3. Checking only the provider's source credentials
would test the wrong identity. Avoid a more privileged profile just to make a gate pass.

A separate `state.inspection_identity` can inspect bucket/KMS configuration. Define
that alias in `[identities]`. The actual state-object check always uses `state.identity`,
so an inspector's access cannot substitute for backend access.

## What the initial modules establish

### Identity

Verifies the effective account and assumed role. Successful `GetCallerIdentity` does
not prove any deployment permission. The SDK session is reused within a run; profile
providers retain their normal credential refresh behavior.

### State

Checks bucket owner (through `ExpectedBucketOwner`), region, versioning, and default
encryption. If `expected_kms_key_arn` is configured, requires SSE-KMS with that exact
full key ARN in the bucket defaults and verifies that the key is enabled. Aliases and
bare key IDs are rejected: an unqualified alias can resolve in a different caller's
account. If omitted, requires SSE-S3/AES256.
This is the **bucket default**; align Terraform's own encryption configuration separately.

- `mode = "existing"` reads and discards one byte from the exact state key. It exercises
  GetObject and, where applicable, KMS decrypt without placing state contents in reports.
- `mode = "new"` verifies that exact key is absent using a prefix-scoped list. An access
  error fails the check; it is never interpreted as absence. An existing object fails.
- The default workspace uses the raw `key`. Other workspaces use
  `workspace_key_prefix/workspace/key`, normalized like Terraform's Go `path.Join`
  (including dot segments and repeated slashes); the prefix defaults to `env:`.

These checks **do not certify PutObject, state locking, or every future Terraform
operation**. They do not write canary objects or touch `.tflock` files. Terraform still
performs locking. Add a separately designed write probe if your contract requires one.
After a first deployment, change `mode` to `existing`. Choose the contract appropriate
to the operation; do not repeatedly run a `new` contract after state has been created.

### Network

Each configured subnet is described through the application identity. Checks validate
visibility, owner account, VPC, exact AZ ID, available state, disabled automatic public
IPv4 assignment, and available IPv4 addresses against additional demand plus reserve.
The manifest requires unique subnet IDs/names and at least `minimum_azs` distinct AZ IDs.
By matching each observed AZ ID, the suite checks the configured AZ placement as well.

Include rollout surge and replacement overlap in `additional_ipv4_required`. This
starter takes that budget explicitly; it does not parse Terraform plans. Capacity is
an observation, not a reservation. It does not inspect route tables, NAT gateways,
endpoint policies, security groups or NACLs. Add those as separate checks.

### Runtime

Add `runtime` to `modules` and configure `[[runtime.probes]]` entries. Each uses
Testinfra to run a bounded `curl` request from an existing host/container and compare
its response body. No probe infrastructure is created automatically.

Supported configured transport prefixes include `local://`, `ssh://`, `docker://`,
`podman://`, `kubectl://`, `ansible://` and `paramiko://`. Install any transport-specific
extras and tools you choose; the initial dependency set supports the local and system
SSH backends. For example, `uv add --project preflight 'pytest-testinfra[paramiko]'`.
The target needs `curl`. Configure SSH host keys and authentication normally.
The probe disables per-user curl configuration files so settings such as `insecure`
cannot silently disable certificate checks. Normal proxy and CA environment settings
still apply; configure trust on the actual probe host.

A pass proves the route, HTTPS certificate trust when applicable, and service response
for the actual network location. It does not prove a future workload's IAM permissions or a different security
group's access. A mandatory probe you cannot run must fail, not skip.

## Inspection permissions

Grant observation permissions deliberately, or use the separate inspection identity.
Do not expand a deployment role to AdministratorAccess to satisfy metadata checks.

| Checks | Required observation actions |
| --- | --- |
| Identity | STS GetCallerIdentity; AWS does not require an Allow for this operation |
| Bucket configuration | `s3:GetBucketLocation`, `s3:GetBucketVersioning`, `s3:GetEncryptionConfiguration` |
| KMS configuration, when requested | `kms:DescribeKey` on the configured bucket key |
| Existing state | `s3:GetObject` on the exact object; applicable `kms:Decrypt` permissions |
| New state | `s3:ListBucket` with the exact state prefix used by the check |
| Subnets | `ec2:DescribeSubnets` |
| Runtime | Existing transport access, target `curl`, and endpoint access |

Bucket policies, key policies, SCPs and endpoint policies can also affect requests.
An unreadable required observation blocks with an error instead of silently passing.

## Add your first module

Create `checks/platform/test_bootstrap_version.py`:

```python
import pytest
from gate.assertions import require

pytestmark = pytest.mark.owner("platform-team")


def test_bootstrap_version(aws, contract):
    settings = contract.settings["platform"]
    response = aws.client("deploy", "ssm").get_parameter(
        Name=settings["version_parameter"], WithDecryption=False
    )
    version = int(response["Parameter"]["Value"])
    require(
        version >= settings["minimum_version"],
        expected=f"bootstrap version >= {settings['minimum_version']}",
        observed=version,
        remediation="Run the platform bootstrap upgrade before this deployment.",
    )
```

Add `platform` to the contract's `modules` list, then add:

```toml
[settings.platform]
version_parameter = "/platform/bootstrap-version"
minimum_version = 3
```

This example assumes a plain String SSM parameter and needs `ssm:GetParameter` access.
It is a template, not an installed check. Use a module-local `conftest.py` for reusable
observations, and optionally validate your custom settings with a Pydantic model there.

Ordinary pytest assertions and parametrization work. `contract` and `aws` are shared
fixtures; Testinfra's `host` fixture is also available, using the local backend by default.
For remote targets use `testinfra.get_host(...)`, as the runtime module demonstrates.

Keep checks independent. A failed fixture blocks only its dependent tests while other
checks continue. Reuse observations with appropriately scoped fixtures, and use paginators
for API listings that can span pages. All tests in a selected module are required: split
optional diagnostics into a separate contract instead of using skip or xfail markers.

## Gate and report behavior

The wrapper blocks on failed assertions, fixture/collection/teardown errors, skips,
xfail, xpass, deselection, empty required modules, missing modules, timeout, interrupted
runs, missing completion reports, and contract changes during execution. It requires
successful setup, call and teardown for each collected test.

It explicitly loads the required plugins, clears inherited `PYTEST_ADDOPTS` and
`PYTEST_PLUGINS`, overrides configured `addopts`, and avoids parent-repository conftests.
If you add a pytest plugin, add it to the explicit list in `gate/cli.py` as well as uv.
Test modules and repository conftests remain trusted Python code; this is not a sandbox
against malicious tests or a user bypassing the wrapper.

Every run gets a unique directory under `preflight/reports/` containing:

- `report.html`: self-contained human-readable pytest report.
- `junit.xml`: CI-compatible test results.
- `gate.json`: contract hash, selected modules, per-stage results, owners, and blocking reasons.
- `runner.json`: additional wrapper failure, when applicable, such as a suite timeout.

Invalid contracts are rejected before pytest and report creation. An abrupt termination
may leave incomplete HTML/XML or no gate.json; the wrapper blocks in that case. Do not
use the pytest HTML headline alone as the gate decision: pytest can call xfail/skip
acceptable, while this wrapper deliberately blocks them.

The gate process returns 0 only for a successful gate (and successful child, if any),
1 for failed/incomplete checks, and 2 for invalid invocation/configuration. After a passing
gate, a child command's exit status is propagated; failure to start it returns 127.
Arguments after `--` are passed directly without shell interpretation. The child retains
the caller's environment and working directory. Compound operations belong in a script.

Default limits are 60 seconds per test and 600 seconds for the suite. Customize with
`--test-timeout` and `--suite-timeout`. AWS clients use bounded connection/read timeouts
and retries. On POSIX the total timeout kills the local pytest process group; remote
commands should retain their own timeouts. Reports are ignored by git and may contain
sensitive operational details from your assertions/output. Do not print secrets.

Re-run volatile checks close to apply, especially after an approval delay. Bootstrap
checks do not lock shared resources. Retain appropriate Terraform preconditions;
Terraform `check` blocks alone do not block an operation when an assertion fails.

## Verify and maintain the scaffold

These self-tests use local pytest subprocesses, stub AWS responses, and a loopback HTTPS
server. They do not connect to your AWS accounts, require AWS credentials, or run
Terraform. The TLS regressions need local `curl` and `openssl`; only those self-tests
skip if these tools are absent. Operational checks under `checks/` still block on skips.

```bash
uv run --project preflight --locked pytest -c preflight/pyproject.toml preflight/tests
uv run --project preflight --locked ruff check preflight
uv run --project preflight --locked ruff format --check preflight
```

Update dependencies intentionally with `uv lock --project preflight --upgrade`, then
run the self-tests and commit the updated lockfile. Keep development self-tests separate
from `checks/`: a passing harness self-test does not establish AWS readiness.

References: [uv projects](https://docs.astral.sh/uv/guides/projects/),
[Testinfra](https://testinfra.readthedocs.io/en/latest/),
[Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3),
[Boto3 credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
