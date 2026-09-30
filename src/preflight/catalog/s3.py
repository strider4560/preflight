"""S3 facts a provider decides on: whether a bucket exists (or belongs to someone else) and
whether an object exists. Observations, not verdicts: an item is ok with what was seen."""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

from preflight.check import check
from preflight.identity import Identity
from preflight.outcome import Outcome, error, ok, outcome
from preflight.probe import Probe

BucketName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")]
ObjectKey = Annotated[str, StringConstraints(min_length=1)]


@check(key="bucket")
def bucket_status(probe: Probe, bucket: BucketName, identity: Identity) -> Outcome:
    """ "present", "absent", or "forbidden" (the name belongs to another account). head-bucket
    is the one call that tells a 403 from a 404, and no Ansible module exposes it."""
    result = probe.host.run(
        "aws s3api head-bucket --bucket %s --region %s", bucket, identity.region
    )
    if result.rc == 127:
        return outcome(error(do="Install the AWS CLI.", error_type="MissingTool"))
    if result.rc == 0:
        return outcome(ok(observed="present"))
    if "(404)" in result.stderr:
        return outcome(ok(observed="absent"))
    if "(403)" in result.stderr:
        return outcome(ok(observed="forbidden"))
    return outcome(
        error(
            do=(
                f"Could not tell whether s3://{bucket} exists; check that profile "
                f"{identity.profile} is signed in and may call s3:ListBucket."
            ),
            error_type="AwsCli",
        )
    )


@check(key="key")
def object_exists(probe: Probe, bucket: BucketName, key: ObjectKey, identity: Identity) -> Outcome:
    """True or False. Keys list in lexical order, so the first key under the prefix `key` is
    `key` itself when it exists; a missing bucket fails the module and is an error."""
    try:
        listing = probe.aws_module(
            "amazon.aws.s3_object_info",
            {"bucket_name": bucket, "prefix": key, "max_keys": 1},
            expect=("s3_keys",),
        )
    except Exception as exc:
        return outcome(
            error(
                do=(
                    f"Could not list s3://{bucket}/{key}; check that profile "
                    f"{identity.profile} may call s3:ListBucket."
                ),
                error_type=type(exc).__name__,
            )
        )
    return outcome(ok(observed=key in (listing.get("s3_keys") or [])))
