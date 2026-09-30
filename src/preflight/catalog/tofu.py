"""A clean plan: exit 0 from `tofu plan -detailed-exitcode`, run read-only (no state lock, no
lockfile writes, a throwaway data directory)."""

from __future__ import annotations

import shutil
import tempfile

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import Outcome, error, fail, ok, outcome
from preflight.probe import Probe

LEFTOVERS = ("backend.tf.off", "terraform.tfstate")


def first_error(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip().lstrip("│").strip()
        if stripped.startswith("Error:"):
            return stripped[:200]
    return None


@check(key="dir", timeout=600)
def plan_clean(
    probe: Probe, dir: str, var_files: tuple[str, ...] = (), identity: Identity | None = None
) -> Outcome:
    directory = probe.path(dir)
    leftovers = [name for name in LEFTOVERS if probe.host.file(str(directory / name)).exists]
    if leftovers:
        return outcome(
            error(
                do=(
                    f"{dir} holds {', '.join(leftovers)} from an interrupted run; a plan there "
                    "would read the wrong state. Finish or clean up that run first."
                ),
                error_type="LeftoverState",
            )
        )
    var_args = [f"-var-file={probe.path(f)}" for f in var_files]
    extra = " %s" * len(var_args)
    data_dir = tempfile.mkdtemp(prefix="preflight-tofu-")
    try:
        init = probe.host.run(
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
                    do=f"`tofu init` failed in {dir}; run it there to see why.",
                    observed=first_error(init.stderr + init.stdout),
                    error_type="TofuInit",
                )
            )
        plan = probe.host.run(
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
        return outcome(
            fail(do=f"{dir} has changes that are not applied; apply them, then rerun this gate.")
        )
    return outcome(
        error(
            do=f"`tofu plan` failed in {dir}; run it there to see why.",
            observed=first_error(plan.stderr + plan.stdout),
            error_type="TofuPlan",
        )
    )
