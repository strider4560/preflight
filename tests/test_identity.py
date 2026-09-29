import pytest
from pydantic import ValidationError

from preflight.identity import Identity, worker_environment

SPEC = {
    "profile": "sandbox",
    "region": "us-east-1",
    "account_id": "111111111111",
    "permission_set": "AWSAdministratorAccess",
}
ARN = (
    "arn:aws:sts::111111111111:assumed-role/"
    "AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d/operator@example.com"
)


def test_a_permission_set_matches_only_its_generated_role_names():
    identity = Identity(**SPEC)
    assert identity.role_matches("AWSReservedSSO_AWSAdministratorAccess_bd9c3ff84c4cd64d")
    assert not identity.role_matches(
        "AWSReservedSSO_AWSAdministratorAccess_ReadOnly_bd9c3ff84c4cd64d"
    )
    assert not identity.role_matches("AWSReservedSSO_AWSAdministratorAccess_x")


def test_matches_an_assumed_role_in_the_expected_account_only():
    identity = Identity(**SPEC)
    assert identity.matches("111111111111", ARN)
    assert not identity.matches("222222222222", ARN.replace("111111111111", "222222222222"))
    assert not identity.matches("111111111111", "arn:aws:iam::111111111111:user/operator")


def test_an_exact_role_name():
    spec = {**SPEC, "role": "Deploy"}
    del spec["permission_set"]
    identity = Identity(**spec)
    assert identity.role_matches("Deploy")
    assert not identity.role_matches("Deploy2")
    assert identity.describe() == "role Deploy in account 111111111111"


@pytest.mark.parametrize("change", [{"role": "Deploy"}, {"permission_set": None}])
def test_exactly_one_of_role_or_permission_set(change):
    with pytest.raises(ValidationError, match="exactly one of role or permission_set"):
        Identity(**{**SPEC, **change})


def test_the_worker_environment_drops_credentials_and_tofu_overrides():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/op",
        "AWS_ACCESS_KEY_ID": "key",
        "AWS_SESSION_TOKEN": "token",
        "AWS_PROFILE": "production",
        "TF_CLI_ARGS_plan": "-target=x",
        "TF_VAR_x": "1",
        "TF_WORKSPACE": "w",
        "TF_PLUGIN_CACHE_DIR": "/cache",
    }
    env = worker_environment(base, Identity(**SPEC), keep_aws=False, bin_dir="/venv/bin")
    assert env == {
        "PATH": "/venv/bin:/usr/bin",
        "HOME": "/home/op",
        "TF_PLUGIN_CACHE_DIR": "/cache",
        "AWS_PROFILE": "sandbox",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_aws_assumed_keeps_the_callers_credentials():
    base = {"PATH": "/usr/bin", "AWS_ACCESS_KEY_ID": "key", "AWS_PROFILE": "production"}
    env = worker_environment(base, Identity(**SPEC), keep_aws=True, bin_dir="/venv/bin")
    assert env["AWS_ACCESS_KEY_ID"] == "key"
    assert env["AWS_PROFILE"] == "production"
    assert env["AWS_REGION"] == "us-east-1"


def test_a_missing_or_empty_path_never_adds_the_current_directory():
    for base in ({}, {"PATH": ""}):
        env = worker_environment(base, None, keep_aws=False, bin_dir="/venv/bin")
        assert env["PATH"] == "/venv/bin"


REDIRECTS = {
    "AWS_ENDPOINT_URL": "http://evil",
    "AWS_ENDPOINT_URL_STS": "http://evil",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/x",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://evil",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN": "t",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/t",
}


def test_redirecting_variables_are_dropped_unless_aws_is_kept():
    base = {"PATH": "/usr/bin", "AWS_CONFIG_FILE": "/cfg", "TF_VAR_x": "1", **REDIRECTS}
    scrubbed = worker_environment(base, Identity(**SPEC), keep_aws=False, bin_dir="/b")
    assert not set(REDIRECTS) & set(scrubbed)
    assert scrubbed["AWS_CONFIG_FILE"] == "/cfg"
    kept = worker_environment(base, Identity(**SPEC), keep_aws=True, bin_dir="/b")
    assert all(kept[name] == value for name, value in REDIRECTS.items())
    assert kept["AWS_CONFIG_FILE"] == "/cfg"
    assert "TF_VAR_x" not in kept


@pytest.mark.parametrize("keep_aws", [False, True])
def test_workers_write_no_bytecode(keep_aws):
    for identity in (None, Identity(**SPEC)):
        env = worker_environment({"PATH": "/usr/bin"}, identity, keep_aws=keep_aws, bin_dir="/b")
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
