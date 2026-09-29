"""AWS calls are stubbed at the SDK boundary, while credential checks are real."""

import boto3
import pytest
from botocore.stub import Stubber

from gate.aws import AwsContexts, ObservationError
from gate.contract import Contract


def sdk_client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def base_contract():
    return Contract.model_validate(
        {
            "schema_version": 1,
            "environment": "prod",
            "region": "us-east-1",
            "modules": ["identity"],
            "identities": {
                "deploy": {
                    "expected_account_id": "111111111111",
                    "expected_role_arn": "arn:aws:iam::111111111111:role/platform/TerraformDeploy",
                }
            },
        }
    )


@pytest.mark.parametrize(
    "account, arn",
    [
        ("222222222222", "arn:aws:sts::222222222222:assumed-role/TerraformDeploy/ci"),
        ("111111111111", "arn:aws:sts::111111111111:assumed-role/Administrator/ci"),
        ("111111111111", "arn:aws:iam::111111111111:user/developer"),
    ],
)
def test_wrong_identity_blocks_service_access(account, arn):
    sts = sdk_client("sts")
    with Stubber(sts) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "Account": account,
                "Arn": arn,
                "UserId": "AROAEXAMPLE:ci",
            },
            {},
        )

        class Session:
            def client(self, service, **kwargs):
                if service != "sts":
                    pytest.fail("service access attempted before identity validation")
                return sts

        contexts = AwsContexts(base_contract(), session_factory=lambda **kwargs: Session())
        with pytest.raises(ObservationError, match="identity"):
            contexts.client("deploy", "ec2")


def test_verified_identity_can_create_service_client():
    sts, ec2 = sdk_client("sts"), sdk_client("ec2")
    with Stubber(sts) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "Account": "111111111111",
                "Arn": "arn:aws:sts::111111111111:assumed-role/TerraformDeploy/ci",
                "UserId": "AROAEXAMPLE:ci",
            },
            {},
        )

        class Session:
            def client(self, service, **kwargs):
                return {"sts": sts, "ec2": ec2}[service]

        contexts = AwsContexts(base_contract(), session_factory=lambda **kwargs: Session())
        result = contexts.client("deploy", "ec2")
        # The credential check is cached, so a subsequent request cannot consume a second STS stub.
        assert contexts.identity("deploy")["Account"] == "111111111111"
        assert result.meta.service_model.service_name == "ec2"


def test_unknown_identity_is_not_replaced_with_default_credentials():
    contexts = AwsContexts(base_contract())
    with pytest.raises(ObservationError, match="Unknown identity"):
        contexts.client("typo", "ec2")
