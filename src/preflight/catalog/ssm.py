"""SSM parameters another stage publishes: present and non-empty. Values are read without
decryption and never reported."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from preflight.check import IdentityRef, Section, check, session_for
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

SsmName = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9_.\-/]+$")]


class PresentSection(Section):
    identity: IdentityRef
    name: SsmName
    how: str | None = None


class ParametersSection(Section):
    identity: IdentityRef
    names: list[SsmName] = Field(min_length=1)
    how: str | None = None


def _observe(ctx, name: str, how: str | None, key: str | None) -> Item:
    try:
        value = ctx.ssm_lookup(name)
    except Exception as exc:
        return error(
            key,
            do=(
                f"Could not read {name}; "
                f"check that profile {ctx.identity.profile} may call ssm:GetParameter."
            ),
            error_type=type(exc).__name__,
        )
    if value:
        return ok(key)
    return fail(key, do=f"SSM parameter {name} is missing or empty.", paste=how, generic=True)


@check("ssm.present", section=PresentSection, requires=[session_for("identity")])
def present(ctx, s: PresentSection) -> Outcome:
    return outcome(_observe(ctx, s.name, s.how, None))


@check("ssm.parameters", section=ParametersSection, requires=[session_for("identity")])
def parameters(ctx, s: ParametersSection) -> Outcome:
    return outcome(*(_observe(ctx, name, s.how, name) for name in s.names))
