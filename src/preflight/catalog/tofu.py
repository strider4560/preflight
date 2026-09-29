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
