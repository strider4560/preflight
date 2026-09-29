import json

from fakes import ROLE_ARN, FakeAnsibleHost, FakeHost, FakeResult, make_ctx
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import aws
from preflight.check import IdentitySection
from preflight.outcome import Status

SECTION = IdentitySection(identity="admin")
OTHER_ARN = ROLE_ARN.replace("111111111111", "222222222222")


def caller(account="111111111111", arn=ROLE_ARN):
    return FakeAnsibleHost({"amazon.aws.aws_caller_info": {"account": account, "arn": arn}})


def listing(stdout="", rc=0):
    return FakeHost([("aws configure list-profiles", FakeResult(rc, stdout))])


def failing_session(tmp_path, host):
    failure = AnsibleException({"failed": True, "msg": "no such profile"})
    ansible = FakeAnsibleHost({"amazon.aws.aws_caller_info": failure})
    return aws.session.observe(make_ctx(tmp_path, host=host, ansible=ansible), SECTION).items[0]


def test_session_missing_profile_gets_the_setup_step(tmp_path):
    item = failing_session(tmp_path, listing("other\n  default \n"))
    assert item.status is Status.ERROR
    assert item.error_type == "MissingProfile"
    assert item.next_step.do == "Profile sandbox is not configured; add it to ~/.aws/config."
    assert item.next_step.paste == "aws configure sso --profile sandbox"


def test_session_listed_profile_gets_the_sign_in_step(tmp_path):
    item = failing_session(tmp_path, listing("other\n  sandbox \n"))
    assert item.error_type == "AnsibleException"
    assert item.next_step.do == "Sign in to profile sandbox."
    assert item.next_step.paste == "aws sso login --profile sandbox"


def test_session_unlistable_profiles_keep_the_sign_in_step(tmp_path):
    item = failing_session(tmp_path, listing("", rc=127))
    assert item.error_type == "AnsibleException"
    assert item.next_step.paste == "aws sso login --profile sandbox"


def test_session_ok_observes_with_the_named_profile(tmp_path):
    ansible = caller()
    result = aws.session.observe(make_ctx(tmp_path, ansible=ansible), SECTION)
    assert result.status is Status.OK
    assert ansible.calls == [
        ("amazon.aws.aws_caller_info", {"region": "us-east-1", "profile": "sandbox"})
    ]


def test_session_mismatch_names_the_expected_role(tmp_path):
    ctx = make_ctx(tmp_path, ansible=caller("222222222222", OTHER_ARN))
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.FAIL
    assert "AWSReservedSSO_AWSAdministratorAccess_*" in item.next_step.do
    assert item.next_step.paste == "aws sso login --profile sandbox"


def test_session_error_keeps_the_module_message_out(tmp_path):
    failure = AnsibleException({"failed": True, "msg": "token expired SECRET"})
    ctx = make_ctx(
        tmp_path,
        host=listing("sandbox\n"),
        ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failure}),
    )
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "AnsibleException"
    assert item.next_step.paste == "aws sso login --profile sandbox"
    assert "SECRET" not in json.dumps(item.to_dict())


def test_session_non_raising_module_failure_is_an_error(tmp_path):
    failed = {"changed": False, "msg": "Couldn't connect to AWS: SECRET"}
    ctx = make_ctx(
        tmp_path,
        host=listing("sandbox\n"),
        ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failed}),
    )
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "ModuleFailed"
    assert item.next_step.paste == "aws sso login --profile sandbox"
    assert "SECRET" not in json.dumps(item.to_dict())


def test_assumed_non_raising_module_failure_is_an_error(tmp_path):
    failed = {"changed": False, "msg": "Couldn't connect to AWS: SECRET"}
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failed}))
    item = aws.assumed.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "ModuleFailed"
    assert "SECRET" not in json.dumps(item.to_dict())


def test_assumed_observes_the_callers_environment_and_names_what_to_change(tmp_path):
    ansible = caller("222222222222", OTHER_ARN)
    environ = {"AWS_ACCESS_KEY_ID": "k", "AWS_SESSION_TOKEN": "t", "AWS_PROFILE": "production"}
    item = aws.assumed.observe(make_ctx(tmp_path, ansible=ansible, environ=environ), SECTION).items[
        0
    ]
    assert aws.assumed.ambient is True
    assert item.status is Status.FAIL
    assert (
        item.next_step.paste
        == "unset AWS_ACCESS_KEY_ID AWS_SESSION_TOKEN\nexport AWS_PROFILE=sandbox"
    )
    assert ansible.calls == [("amazon.aws.aws_caller_info", {"region": "us-east-1"})]


def test_assumed_ok(tmp_path):
    ctx = make_ctx(tmp_path, ansible=caller(), environ={"AWS_PROFILE": "sandbox"})
    assert aws.assumed.observe(ctx, SECTION).status is Status.OK


def test_assumed_without_credentials_is_an_error_with_the_fix(tmp_path):
    ctx = make_ctx(
        tmp_path,
        ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": AnsibleException({"failed": True})}),
    )
    item = aws.assumed.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.next_step.paste == "export AWS_PROFILE=sandbox\naws sso login --profile sandbox"


def test_region(tmp_path):
    good = FakeHost([("aws configure get region --profile sandbox", FakeResult(0, "us-east-1\n"))])
    unset = FakeHost([("aws configure get region", FakeResult(1, ""))])
    missing = FakeHost([("aws configure get region", FakeResult(127, ""))])
    assert aws.region.observe(make_ctx(tmp_path, host=good), SECTION).status is Status.OK
    item = aws.region.observe(make_ctx(tmp_path, host=unset), SECTION).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "aws configure set region us-east-1 --profile sandbox"
    item = aws.region.observe(make_ctx(tmp_path, host=missing), SECTION).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "MissingTool")


def test_assumed_unsets_variables_that_override_the_profile(tmp_path):
    environ = {
        "AWS_ROLE_ARN": "r",
        "AWS_ENDPOINT_URL_STS": "http://x",
        "AWS_ENDPOINT_URL_S3": "http://y",
        "AWS_PROFILE": "sandbox",
    }
    ctx = make_ctx(tmp_path, ansible=caller("222222222222", OTHER_ARN), environ=environ)
    item = aws.assumed.observe(ctx, SECTION).items[0]
    assert item.next_step.paste == "unset AWS_ROLE_ARN AWS_ENDPOINT_URL_S3 AWS_ENDPOINT_URL_STS"


def test_assumed_mismatch_with_nothing_to_change_blames_the_profile(tmp_path):
    ctx = make_ctx(
        tmp_path, ansible=caller("222222222222", OTHER_ARN), environ={"AWS_PROFILE": "sandbox"}
    )
    item = aws.assumed.observe(ctx, SECTION).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "aws sso login --profile sandbox"
    assert "~/.aws/config" in item.next_step.do


def test_region_other_rc_is_an_error_and_other_region_fails(tmp_path):
    broken = FakeHost([("aws configure get region", FakeResult(255, ""))])
    item = aws.region.observe(make_ctx(tmp_path, host=broken), SECTION).items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "AwsCli")
    assert item.next_step.paste == "aws configure sso --profile sandbox"
    other = FakeHost([("aws configure get region", FakeResult(0, "eu-west-1\n"))])
    item = aws.region.observe(make_ctx(tmp_path, host=other), SECTION).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == "aws configure set region us-east-1 --profile sandbox"
