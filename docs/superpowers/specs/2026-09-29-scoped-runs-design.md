# Scoped runs: a program that acts after its guards — design

| | |
|---|---|
| Date | 2026-09-29 |
| Status | Approved in conversation; awaiting the written review |
| Extends | `2026-09-29-preflight-gate-programs-design.md` (gates as programs) |
| Design driver | `tellabsadmin/iac`'s `scripts/bootstrap.sh`, rewritten as `scripts/bootstrap.py` |

## Why

`Gate.run()` is terminal: it runs the guards, prints the worklist and exits. A gate program that is also the action, such as iac's account bootstrap, needs to continue when every guard passed, with the values its providers resolved (the identity, the decision whether this is a first run) still in hand, and to verify post-conditions at the end. FastAPI's answer is scope: dependencies live for the request. Preflight's is a scoped run.

The library still never runs the command it gates. The consumer's program does, in its own code, after `preflight` has said the way is clear.

## `Gate.checked()`

```python
if __name__ == "__main__":
    with gate.checked(sys.argv[1:]) as run:
        env, plan = run.args["env"], run[state_plan]
        apply(env, plan)
        run.verify(iac.bootstrap_published)
```

- `gate.checked(argv=None, *, executor=None, root=None)` is a context manager. Up to the verdict it does what `run()` does: parse the command line, run the guards in order, print the worklist. If a guard stopped the run, or the gate is wrong, it raises `SystemExit` with `run()`'s code (1, 2, 3, 130) and never enters the block. Under `--validate` it prints "Validated every guard; nothing was observed." and exits 0 without entering the block.
- When every guard passed it enters the block with a `Run`, and the providers stay alive: their `yield` cleanup has not run, and `probe_now` still works.
  - `run.args` is the parsed command line, by argument name.
  - `run[provider]` is the value the provider resolved, resolving it now if no guard needed it. An `Unmet` raised there prints the provider's steps as a one-guard worklist and exits 1.
  - `run.verify(guards)` runs another `Guards` (a `Gate`'s guards included) in the same scope, prints their worklist, and raises `SystemExit(1)` when one stopped (2 or 3 for the same reasons as `run()`); when every guard passed it prints "Every guard passed." and returns. A program may call it more than once.
- Leaving the block, however it ends, runs the providers' cleanup in reverse order, as at the end of `run()`. A cleanup that raises is printed to stderr and turns a passing exit into 3.
  - A `SystemExit` from the block (a `verify` that stopped, or the program's own) propagates with its code.
  - Ctrl-C, SIGTERM or SIGHUP kill every live worker and exit 130.
  - An `Unmet` raised in the block itself (an `observed` the program calls) prints its steps as a one-guard worklist, `needs the program`, and exits 1.
  - Any other exception from the block is the program's own failure: `<prog>: the program raised <Type>` on stderr, exit 2, the type only. A `GateDefinitionError` (such as `run.verify` given a guard whose argument the gate lacks) prints its problems instead, which name arguments and guards, never values.

`run()` is unchanged in behavior and shares the same run loop, so a gate that is only a gate keeps working.

## Observations

A provider that needs facts rather than a verdict reads them from a check's outcome: `probe_now(check(...))` returns the `Outcome`, and each item carries `observed`. Two catalog checks are added for that use, always `ok` with an observed value (`error` when the fact cannot be read):

| Check | Arguments | Observes |
|---|---|---|
| `s3.bucket_status` | `bucket`, `identity` | `"present"`, `"absent"` or `"forbidden"` (403: the name belongs to another account). No Ansible module distinguishes 403 from 404, so this check runs `aws s3api head-bucket` through `probe.host.run` and reads the status code from its stderr; the message itself never leaves the worker |
| `s3.object_exists` | `bucket`, `key`, `identity` | `True` or `False`, through `amazon.aws.s3_object_info` |

A helper, `preflight.observed(outcome, key=None)`, returns the observed value of an `ok` item and raises `Unmet(outcome)` when the item is not ok, so a provider can write `status = observed(probe_now(s3.bucket_status(bucket, identity=identity)))`. Under `--validate` nothing is observed: every item's observed value is `preflight.NOT_OBSERVED` (a `str`), and `observed` returns it for any key. A provider treats it as unknown, never as present or absent, and never refuses on it.

## iac's bootstrap as a program

`scripts/bootstrap.py` replaces `scripts/bootstrap.sh` and keeps its behavior: safe to rerun; a first run applies with local state and migrates it into the bucket it created; a later run applies against the remote state; the apply asks for approval; the run ends with the milestone's verification.

- **Guards**, in order: `iac.ids_filled`; "this shell is the account's administrator" (`aws.assumed`, `aws.region`); "the checkout holds the latest main" (`git.up_to_date`); "the state is where the plan expects it" (one iac check that restates the observed plan as an `ok` item: bucket status, local state, state object).
- **`state_plan`**, a provider, observes the bucket status, the local `terraform.tfstate` and the state object, and decides. The three refusals raise `Unmet` with the shell's messages: a forbidden bucket name ("set `state_bucket_suffix`"), a present bucket with local state and a state object ("refusing to overwrite; compare the two by hand"), a present bucket with no local state and no object ("import the existing resources by hand"). `first_run` is "the bucket is absent, or local state exists".
- **The block** restores a leftover `backend.tf.off` first, then runs the shell's tofu steps one for one through `subprocess.run(..., check=True, cwd=STACK)` with inherited stdio, restoring `backend.tf` in a `finally` as the shell's `trap` did, and ends with `run.verify(iac.bootstrap_published)`.
- **`iac.bootstrap_published`** is a `Guards()` holding the milestone's "bootstrap published" guard; `preflight/bootstrap.py` includes it, so "is bootstrap applied?" and the program's verification are one definition. `preflight/bootstrap_entry.py` is retired.
- `task preflight:validate` and CI validate `preflight/bootstrap.py` and `scripts/bootstrap.py` in both environments. README and AGENTS.md name `scripts/bootstrap.py`.

## Release

`checked`, `Run`, `observed`, `NOT_OBSERVED` and the `s3` checks are new public API: `v0.2.0`. iac pins it.

## Acceptance

1. Library tests and lint pass; an end-to-end test runs a scratch program through `checked`, acts in the block, and verifies.
2. iac: `scripts/bootstrap.py dev --validate` and `prod --validate` pass; `scripts/bootstrap.py dev` under Production credentials stops at the administrator guard; under `sandbox` it reaches `tofu apply`'s approval prompt, and answering `no` exits nonzero without applying; `preflight/bootstrap.py` still passes in both environments.
