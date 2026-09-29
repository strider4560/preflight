"""Read-only backend evidence. Write and lock permissions remain unproven."""

import pytest

from gate.assertions import require

pytestmark = pytest.mark.owner("cloud-platform")


def inspection_client(aws, state, service):
    return aws.client(
        state.inspection_identity or state.identity, service, region_name=state.region
    )


def test_bucket_region(aws, contract):
    state = contract.state
    observed = (
        inspection_client(aws, state, "s3")
        .get_bucket_location(Bucket=state.bucket, ExpectedBucketOwner=state.owner_account_id)
        .get("LocationConstraint")
    )
    observed = {"EU": "eu-west-1", None: "us-east-1"}.get(observed, observed)
    require(
        observed == state.region,
        expected=f"state bucket region {state.region}",
        observed=observed,
        remediation="Correct the backend region or bootstrap bucket selection.",
    )


def test_bucket_versioning(aws, contract):
    state = contract.state
    observed = (
        inspection_client(aws, state, "s3")
        .get_bucket_versioning(Bucket=state.bucket, ExpectedBucketOwner=state.owner_account_id)
        .get("Status", "NotEnabled")
    )
    require(
        not state.require_versioning or observed == "Enabled",
        expected="Enabled versioning" if state.require_versioning else "versioning is optional",
        observed=observed,
        remediation="Enable versioning through the bootstrap owner.",
    )


def test_bucket_default_encryption(aws, contract):
    state = contract.state
    rules = inspection_client(aws, state, "s3").get_bucket_encryption(
        Bucket=state.bucket, ExpectedBucketOwner=state.owner_account_id
    )["ServerSideEncryptionConfiguration"]["Rules"]
    require(
        len(rules) == 1,
        expected="one default encryption rule",
        observed=len(rules),
        remediation="Review bucket default encryption with its owner.",
    )
    rule = rules[0]["ApplyServerSideEncryptionByDefault"]
    algorithm = "aws:kms" if state.expected_kms_key_arn else "AES256"
    require(
        rule["SSEAlgorithm"] == algorithm,
        expected=algorithm,
        observed=rule["SSEAlgorithm"],
        remediation="Align bootstrap and backend encryption settings.",
    )
    if state.expected_kms_key_arn:
        configured_key = rule.get("KMSMasterKeyID")
        require(
            configured_key == state.expected_kms_key_arn,
            expected=f"full KMS key ARN {state.expected_kms_key_arn} in bucket defaults",
            observed=configured_key,
            remediation=(
                "Use the full key ARN; unqualified aliases resolve in the requester's account."
            ),
        )
        metadata = inspection_client(aws, state, "kms").describe_key(KeyId=configured_key)[
            "KeyMetadata"
        ]
        require(
            metadata["Arn"] == state.expected_kms_key_arn
            and metadata["KeyState"] == "Enabled"
            and metadata["Enabled"],
            expected=f"enabled KMS key {state.expected_kms_key_arn}",
            observed=f"{metadata['Arn']}: {metadata['KeyState']}",
            remediation="Correct the bucket key or restore its enabled state through its owner.",
        )


def test_state_object_access(aws, contract):
    state = contract.state
    # Use the BACKEND identity here, even when an inspection identity was configured.
    s3 = aws.client(state.identity, "s3", region_name=state.region)
    common = {"Bucket": state.bucket, "ExpectedBucketOwner": state.owner_account_id}
    if state.mode == "new":
        response = s3.list_objects_v2(**common, Prefix=state.effective_key, MaxKeys=1)
        exists = any(item["Key"] == state.effective_key for item in response.get("Contents", []))
        require(
            not exists,
            expected=f"unused state key {state.effective_key}",
            observed="state already exists" if exists else "absent",
            remediation="Use mode='existing' for an existing stack, or correct the state path.",
        )
    else:
        # One byte exercises GetObject/KMS decrypt without copying state into reports.
        response = s3.get_object(**common, Key=state.effective_key, Range="bytes=0-0")
        with response["Body"] as body:
            body.read()
