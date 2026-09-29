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
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failure}))
    item = aws.session.observe(ctx, SECTION).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == "AnsibleException"
    assert item.next_step.paste == "aws sso login --profile sandbox"
    assert "SECRET" not in json.dumps(item.to_dict())


def test_session_non_raising_module_failure_is_an_error(tmp_path):
    failed = {"changed": False, "msg": "Couldn't connect to AWS: SECRET"}
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({"amazon.aws.aws_caller_info": failed}))
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
