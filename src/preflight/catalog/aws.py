"""AWS sessions: can preflight observe as an identity (`aws.session`), will the caller's next
command act as it (`aws.assumed`), and is the profile's region the expected one (`aws.region`)."""

from __future__ import annotations

import shlex

from preflight.check import IdentitySection, check, session_for
from preflight.outcome import Outcome, error, fail, ok, outcome

__all__ = ["assumed", "region", "session", "session_for"]

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


def _caller(ctx, *, ambient: bool) -> tuple[str, str]:
    info = ctx.aws_module(
        "amazon.aws.aws_caller_info", {}, ambient=ambient, expect=("account", "arn")
    )
    return str(info.get("account", "")), str(info.get("arn", ""))


def _profile_missing(ctx, profile: str) -> bool:
    """True only when the CLI lists profiles and this one is not among them."""
    listing = ctx.host.run("aws configure list-profiles")
    if listing.rc != 0:
        return False
    return profile not in [line.strip() for line in listing.stdout.splitlines()]


@check("aws.session", binds="identity")
def session(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    profile = identity.profile
    login = f"aws sso login --profile {shlex.quote(profile)}"
    try:
        account, arn = _caller(ctx, ambient=False)
    except Exception as exc:
        if _profile_missing(ctx, profile):
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
                f"{account or 'unknown'}, but this contract expects {identity.describe()}. "
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


@check("aws.assumed", binds="identity", ambient=True)
def assumed(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    fix = _environment_fix(ctx.environ, identity)
    login = f"aws sso login --profile {identity.profile}"
    try:
        account, arn = _caller(ctx, ambient=True)
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


@check("aws.region", binds="identity")
def region(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    result = ctx.host.run("aws configure get region --profile %s", identity.profile)
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
