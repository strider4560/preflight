"""AWS sessions: can preflight observe as an identity (`aws.session`), will the caller's next
command act as it (`aws.assumed`), and is the profile's region the expected one (`aws.region`)."""

from __future__ import annotations

from preflight.check import IdentitySection, check, session_for
from preflight.outcome import Outcome, error, fail, ok, outcome

__all__ = ["assumed", "region", "session", "session_for"]

CREDENTIAL_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
)


def _caller(ctx, *, ambient: bool) -> tuple[str, str]:
    info = ctx.aws_module(
        "amazon.aws.aws_caller_info", {}, ambient=ambient, expect=("account", "arn")
    )
    return str(info.get("account", "")), str(info.get("arn", ""))


@check("aws.session", binds="identity")
def session(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    login = f"aws sso login --profile {identity.profile}"
    try:
        account, arn = _caller(ctx, ambient=False)
    except Exception as exc:
        return outcome(
            error(
                do=f"Sign in to profile {identity.profile}.",
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
    stray = [name for name in CREDENTIAL_VARIABLES if name in environ]
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
    return outcome(
        fail(
            do=(
                f"Your shell acts as {arn} in account {account}; the next command needs "
                f"{identity.describe()}."
            ),
            paste=fix or f"export AWS_PROFILE={identity.profile}",
            observed=observed,
        )
    )


@check("aws.region", binds="identity")
def region(ctx, s: IdentitySection) -> Outcome:
    identity = ctx.identity
    result = ctx.host.run("aws configure get region --profile %s", identity.profile)
    if result.rc == 127:
        return outcome(error(do="Install the AWS CLI.", error_type="MissingTool"))
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
