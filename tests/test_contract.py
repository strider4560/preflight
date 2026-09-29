import pytest
from pydantic import ValidationError

from gate.contract import Contract, State


@pytest.mark.parametrize(
    "changes",
    [
        {"modules": []},
        {"modules": ["identity"]},
        {"modules": ["state"]},
        {"modules": ["network"]},
        {"modules": ["runtime"]},
        {"modules": ["../other"]},
        {"modules": ["custom", "custom"]},
        {"schema_version": 2},
        {"unknown": True},
    ],
)
def test_incomplete_contracts_are_rejected(changes):
    data = {
        "schema_version": 1,
        "environment": "prod",
        "region": "us-east-1",
        "modules": ["custom"],
    }
    data.update(changes)
    with pytest.raises(ValidationError):
        Contract.model_validate(data)


def test_default_and_named_workspace_paths():
    data = {
        "identity": "state",
        "bucket": "example",
        "owner_account_id": "111111111111",
        "region": "us-east-1",
        "key": "app/terraform.tfstate",
        "mode": "existing",
    }
    assert State.model_validate(data).effective_key == "app/terraform.tfstate"
    data.update(workspace="uat", workspace_key_prefix="workspaces")
    assert State.model_validate(data).effective_key == "workspaces/uat/app/terraform.tfstate"


@pytest.mark.parametrize(
    "workspace, prefix, key, expected",
    [
        ("prod", "workspaces", "app/./state", "workspaces/prod/app/state"),
        ("prod", "workspaces", "app/../state", "workspaces/prod/state"),
        ("prod", "workspaces/", "/app//state", "workspaces/prod/app/state"),
        ("prod", "//workspaces", "app/state", "/workspaces/prod/app/state"),
        ("prod", "", "app/state", "prod/app/state"),
        ("default", "workspaces", "app/./state", "app/./state"),
    ],
)
def test_state_key_matches_terraform_path_join(workspace, prefix, key, expected):
    spec = State(
        identity="backend",
        bucket="example-state",
        owner_account_id="222222222222",
        region="us-east-1",
        key=key,
        workspace=workspace,
        workspace_key_prefix=prefix,
        mode="new",
    )
    assert spec.effective_key == expected


def test_expected_kms_key_requires_a_full_key_arn():
    with pytest.raises(ValidationError):
        State(
            identity="backend",
            bucket="example-state",
            owner_account_id="222222222222",
            region="us-east-1",
            key="state",
            mode="new",
            expected_kms_key_arn="alias/state",
        )
