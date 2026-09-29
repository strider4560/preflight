"""Exercise the shipped checks against actual botocore clients with stubbed responses."""

import io
from types import SimpleNamespace

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from checks.network.test_subnets import test_subnet_contract as check_subnet
from checks.state.test_backend import test_bucket_default_encryption as check_encryption
from checks.state.test_backend import test_state_object_access as check_state_object
from gate.contract import Network, State


def client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


class Context:
    def __init__(self, sdk_client):
        self.sdk_client = sdk_client

    def client(self, alias, service, **kwargs):
        return self.sdk_client


def network():
    return Network.model_validate(
        {
            "identity": "deploy",
            "vpc_id": "vpc-0123456789abcdef0",
            "owner_account_id": "222222222222",
            "minimum_azs": 1,
            "subnets": [
                {
                    "name": "app-a",
                    "id": "subnet-0123456789abcdef0",
                    "az_id": "use1-az1",
                    "additional_ipv4_required": 40,
                    "reserve_ipv4": 40,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "available, owner, succeeds",
    [
        (80, "222222222222", True),
        (79, "222222222222", False),
        (100, "333333333333", False),
    ],
)
def test_subnet_owner_and_capacity_are_enforced(available, owner, succeeds):
    expected = network()
    ec2 = client("ec2")
    subnet = {
        "SubnetId": expected.subnets[0].id,
        "VpcId": expected.vpc_id,
        "OwnerId": owner,
        "AvailabilityZoneId": "use1-az1",
        "AvailabilityZone": "us-east-1a",
        "State": "available",
        "CidrBlock": "10.0.1.0/24",
        "AvailableIpAddressCount": available,
        "MapPublicIpOnLaunch": False,
        "DefaultForAz": False,
    }
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_subnets", {"Subnets": [subnet]}, {"SubnetIds": [expected.subnets[0].id]}
        )
        args = (Context(ec2), SimpleNamespace(network=expected), expected.subnets[0])
        if succeeds:
            check_subnet(*args)
        else:
            with pytest.raises(pytest.fail.Exception):
                check_subnet(*args)


def state(mode):
    return State.model_validate(
        {
            "identity": "state",
            "bucket": "example-state",
            "owner_account_id": "222222222222",
            "region": "us-east-1",
            "key": "app/terraform.tfstate",
            "workspace": "prod",
            "mode": mode,
        }
    )


def test_new_state_checks_exact_workspace_key_and_accepts_absence():
    spec = state("new")
    s3 = client("s3")
    with Stubber(s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {"Contents": []},
            {
                "Bucket": "example-state",
                "ExpectedBucketOwner": "222222222222",
                "Prefix": "env:/prod/app/terraform.tfstate",
                "MaxKeys": 1,
            },
        )
        check_state_object(Context(s3), SimpleNamespace(state=spec))


def test_new_state_refuses_existing_object():
    spec = state("new")
    s3 = client("s3")
    with Stubber(s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "env:/prod/app/terraform.tfstate"}],
            },
            {
                "Bucket": "example-state",
                "ExpectedBucketOwner": "222222222222",
                "Prefix": "env:/prod/app/terraform.tfstate",
                "MaxKeys": 1,
            },
        )
        with pytest.raises(pytest.fail.Exception, match="already exists"):
            check_state_object(Context(s3), SimpleNamespace(state=spec))


def test_existing_state_reads_without_recording_payload():
    spec = state("existing")
    s3 = client("s3")
    raw_stream = io.BytesIO(b"{")
    stream = StreamingBody(raw_stream, 1)
    with Stubber(s3) as stub:
        stub.add_response(
            "get_object",
            {"Body": stream},
            {
                "Bucket": "example-state",
                "ExpectedBucketOwner": "222222222222",
                "Key": "env:/prod/app/terraform.tfstate",
                "Range": "bytes=0-0",
            },
        )
        check_state_object(Context(s3), SimpleNamespace(state=spec))
    assert raw_stream.closed


def test_new_state_does_not_treat_access_denied_as_absence():
    from botocore.exceptions import ClientError

    spec = state("new")
    s3 = client("s3")
    with Stubber(s3) as stub:
        stub.add_client_error("list_objects_v2", "AccessDenied")
        with pytest.raises(ClientError):
            check_state_object(Context(s3), SimpleNamespace(state=spec))


def test_new_state_refuses_existing_object_at_terraform_normalized_key():
    spec = state("new").model_copy(update={"key": "app/./terraform.tfstate"})
    s3 = client("s3")
    canonical_key = "env:/prod/app/terraform.tfstate"
    with Stubber(s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": canonical_key}]},
            {
                "Bucket": "example-state",
                "ExpectedBucketOwner": "222222222222",
                "Prefix": canonical_key,
                "MaxKeys": 1,
            },
        )
        with pytest.raises(pytest.fail.Exception, match="already exists"):
            check_state_object(Context(s3), SimpleNamespace(state=spec))


@pytest.mark.parametrize("use_alias", [True, False])
def test_bucket_key_is_unambiguous_across_backend_and_inspector_accounts(use_alias):
    key_id = "12345678-1234-1234-1234-123456789abc"
    key_arn = f"arn:aws:kms:us-east-1:222222222222:key/{key_id}"
    spec = state("new").model_copy(
        update={
            "identity": "backend",
            "inspection_identity": "bucket-owner",
            "expected_kms_key_arn": key_arn,
        }
    )
    configured_key = "alias/state" if use_alias else key_arn
    s3, kms = client("s3"), client("kms")

    class SplitContext:
        def client(self, alias, service, **kwargs):
            assert alias == "bucket-owner"
            return {"s3": s3, "kms": kms}[service]

    with Stubber(s3) as s3_stub, Stubber(kms) as kms_stub:
        s3_stub.add_response(
            "get_bucket_encryption",
            {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": configured_key,
                            }
                        }
                    ]
                }
            },
            {"Bucket": "example-state", "ExpectedBucketOwner": "222222222222"},
        )
        # An unqualified alias resolves successfully in the inspector's account,
        # but that says nothing about its meaning in the backend caller's account.
        kms_stub.add_response(
            "describe_key",
            {
                "KeyMetadata": {
                    "KeyId": key_id,
                    "Arn": key_arn,
                    "KeyState": "Enabled",
                    "Enabled": True,
                }
            },
            {"KeyId": configured_key},
        )
        if use_alias:
            with pytest.raises(pytest.fail.Exception, match="full KMS key ARN"):
                check_encryption(SplitContext(), SimpleNamespace(state=spec))
        else:
            check_encryption(SplitContext(), SimpleNamespace(state=spec))
            kms_stub.assert_no_pending_responses()
