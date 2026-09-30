"""SSM parameters another stage publishes: present and non-empty. Values are read without
decryption and never reported."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from preflight.check import UniqueList, check
from preflight.identity import Identity
from preflight.outcome import Item, Outcome, error, fail, ok, outcome
from preflight.probe import Probe

SsmName = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_.\-/]+$")]
Names = Annotated[UniqueList[SsmName], Field(min_length=1)]


def _observe(probe: Probe, identity: Identity, name: str) -> Item:
    try:
        value = probe.ssm_lookup(name)
    except Exception as exc:
        return error(
            name,
            do=(
                f"Could not read {name}; "
                f"check that profile {identity.profile} may call ssm:GetParameter."
            ),
            error_type=type(exc).__name__,
        )
    if value:
        return ok(name)
    return fail(
        name,
        do=(
            f"SSM parameter {name} does not exist in account {identity.account_id} "
            f"({identity.region}), or is empty. Publish it from the stack that owns it, "
            "then confirm:"
        ),
        paste=(
            f"aws ssm get-parameter --name {name} --profile {identity.profile} "
            f"--region {identity.region} --query Parameter.Name --output text"
        ),
    )


@check
def parameters_exist(probe: Probe, names: Names, identity: Identity) -> Outcome:
    return outcome(*(_observe(probe, identity, name) for name in names))
