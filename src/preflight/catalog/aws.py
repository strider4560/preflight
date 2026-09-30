"""AWS sessions: can preflight observe as an identity (`aws.session`), will the caller's next
command act as it (`aws.assumed`), is the profile's region the expected one (`aws.region`), and
`signed_in`, the helper a gate's provider uses to yield a verified identity."""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import Outcome, Status, error, fail, ok, outcome
from preflight.params import Unmet
from preflight.probe import Probe
from preflight.runner import probe_now

__all__ = ["Identity", "assumed", "region", "session", "signed_in"]

CREDENTIAL_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
)
# Other variables that make the CLI or SDK ignore the selected profile.
PROFILE_OVERRIDES = (
    "AWS_ROLE_ARN",
    "AWS_ROLE_SESSION_NAME",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_ENDPOINT_URL",
)


def _caller(probe: Probe, *, ambient: bool) -> tuple[str, str]:
    info = probe.aws_module(
        "amazon.aws.aws_caller_info", {}, ambient=ambient, expect=("account", "arn")
    )
    return str(info.get("account", "")), str(info.get("arn", ""))


def _profile_missing(probe: Probe, profile: str) -> bool:
    """True only when the CLI lists profiles and this one is not among them."""
    listing = probe.host.run("aws configure list-profiles")
    if listing.rc != 0:
        return False
    return profile not in [line.strip() for line in listing.stdout.splitlines()]


@check
def session(probe: Probe, identity: Identity) -> Outcome:
    profile = identity.profile
    login = f"aws sso login --profile {shlex.quote(profile)}"
    try:
        account, arn = _caller(probe, ambient=False)
    except Exception as exc:
        if _profile_missing(probe, profile):
            return outcome(
                error(
                    do=f"Profile {profile} is not configured; add it to ~/.aws/config.",
                    paste=f"aws configure sso --profile {shlex.quote(profile)}",
                    error_type="MissingProfile",
                )
            )
        return outcome(
            error(
                do=f"Sign in to profile {profile}.",
                paste=login,
                error_type=type(exc).__name__,
            )
        )
    observed = {"account": account, "arn": arn}
    if identity.matches(account, arn):
        return outcome(ok(observed=observed))
    return outcome(
        fail(
            do=(
                f"Profile {identity.profile} signs in as {arn or 'nothing'} in account "
                f"{account or 'unknown'}, but this gate expects {identity.describe()}. "
                "Point the profile at that account and permission set in ~/.aws/config, "
                "then sign in again."
            ),
            paste=login,
            observed=observed,
        )
    )


def _environment_fix(environ, identity) -> str:
    lines = []
    stray = [name for name in CREDENTIAL_VARIABLES + PROFILE_OVERRIDES if name in environ]
    stray += sorted(name for name in environ if name.startswith("AWS_ENDPOINT_URL_"))
    if stray:
        lines.append("unset " + " ".join(stray))
    if environ.get("AWS_PROFILE") != identity.profile:
        lines.append(f"export AWS_PROFILE={identity.profile}")
    return "\n".join(lines)


@check(ambient=True)
def assumed(probe: Probe, identity: Identity) -> Outcome:
    fix = _environment_fix(probe.environ, identity)
    login = f"aws sso login --profile {identity.profile}"
    try:
        account, arn = _caller(probe, ambient=True)
    except Exception as exc:
        return outcome(
            error(
                do=(
                    "Your shell has no working AWS credentials; "
                    f"select profile {identity.profile} and sign in."
                ),
                paste=f"{fix}\n{login}" if fix else login,
                error_type=type(exc).__name__,
            )
        )
    observed = {"account": account, "arn": arn}
    if identity.matches(account, arn):
        return outcome(ok(observed=observed))
    if not fix:
        return outcome(
            fail(
                do=(
                    f"Your shell uses profile {identity.profile}, which signs in as {arn} in "
                    f"account {account}; the next command needs {identity.describe()}. "
                    f"Check profile {identity.profile} in ~/.aws/config, then sign in again."
                ),
                paste=login,
                observed=observed,
            )
        )
    return outcome(
        fail(
            do=(
                f"Your shell acts as {arn} in account {account}; the next command needs "
                f"{identity.describe()}."
            ),
            paste=fix,
            observed=observed,
        )
    )


@check
def region(probe: Probe, identity: Identity) -> Outcome:
    result = probe.host.run("aws configure get region --profile %s", identity.profile)
    if result.rc == 127:
        return outcome(error(do="Install the AWS CLI.", error_type="MissingTool"))
    if result.rc not in (0, 1):
        return outcome(
            error(
                do=f"Profile {identity.profile} is not configured; add it to ~/.aws/config.",
                paste=f"aws configure sso --profile {identity.profile}",
                error_type="AwsCli",
            )
        )
    configured = result.stdout.strip()
    if result.rc == 0 and configured == identity.region:
        return outcome(ok(observed=configured))
    return outcome(
        fail(
            do=(
                f"Profile {identity.profile} has region {configured or 'unset'}; "
                f"set it to {identity.region}."
            ),
            paste=f"aws configure set region {identity.region} --profile {identity.profile}",
            observed=configured or None,
        )
    )


@contextmanager
def signed_in(**fields: Any) -> Iterator[Identity]:
    """Yields the identity once its profile signs in as it; otherwise raises Unmet carrying
    `aws.session`'s next step (for example the `aws sso login` to paste)."""
    identity = Identity(**fields)
    result = probe_now(session(identity=identity))
    if result.status is not Status.OK:
        raise Unmet(result)
    yield identity
