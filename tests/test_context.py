import os
import sys

import pytest
from fakes import FakeAnsibleHost, make_ctx

from preflight import context as context_module


def test_aws_modules_get_the_profile_and_region(tmp_path):
    ansible = FakeAnsibleHost({"amazon.aws.aws_caller_info": {"account": "1"}})
    ctx = make_ctx(tmp_path, ansible=ansible)
    assert ctx.aws_module("amazon.aws.aws_caller_info") == {"account": "1"}
    ctx.aws_module("amazon.aws.aws_caller_info", {"x": 1}, ambient=True)
    assert ansible.calls == [
        ("amazon.aws.aws_caller_info", {"region": "us-east-1", "profile": "sandbox"}),
        ("amazon.aws.aws_caller_info", {"x": 1, "region": "us-east-1"}),
    ]


@pytest.mark.parametrize(("msg", "expected"), [("value", "value"), ("", None), ("None", None)])
def test_ssm_lookup_reads_without_decryption(tmp_path, msg, expected):
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"msg": msg}})
    assert make_ctx(tmp_path, ansible=ansible).ssm_lookup("/platform/state/bucket") == expected
    ((module, args),) = ansible.calls
    assert module == "ansible.builtin.debug"
    assert args["msg"] == (
        "{{ lookup('amazon.aws.ssm_parameter', '/platform/state/bucket', decrypt=False, "
        "on_missing='skip', profile='sandbox', region='us-east-1') }}"
    )


def test_ssm_lookup_refuses_unsafe_names(tmp_path):
    ctx = make_ctx(tmp_path, ansible=FakeAnsibleHost({}))
    with pytest.raises(ValueError):
        ctx.ssm_lookup("/x', profile='other")


def test_a_check_without_an_identity_cannot_call_aws(tmp_path):
    with pytest.raises(RuntimeError, match="no identity"):
        make_ctx(tmp_path, identity=None, ansible=FakeAnsibleHost({})).aws_module("m")


def test_the_ansible_host_uses_a_local_inventory_with_this_python(monkeypatch):
    captured = {}

    def fake_get_host(spec, **kwargs):
        captured["spec"] = spec
        captured["inventory"] = open(kwargs["ansible_inventory"]).read()
        return "host"

    monkeypatch.setattr(context_module.testinfra, "get_host", fake_get_host)
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    assert context_module.make_ansible_host() == "host"
    assert captured["spec"] == "ansible://localhost"
    assert captured["inventory"] == (
        f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n"
    )
    assert os.path.isfile(os.environ["ANSIBLE_CONFIG"])


def test_aws_module_raises_when_an_expected_key_is_missing(tmp_path):
    failed = {"msg": "Couldn't connect to AWS", "changed": False}
    ansible = FakeAnsibleHost({"amazon.aws.aws_caller_info": failed})
    ctx = make_ctx(tmp_path, ansible=ansible)
    with pytest.raises(context_module.ModuleFailed) as raised:
        ctx.aws_module("amazon.aws.aws_caller_info", expect=("account", "arn"))
    assert str(raised.value) == "amazon.aws.aws_caller_info"


def test_aws_module_raises_when_the_result_says_failed(tmp_path):
    ansible = FakeAnsibleHost({"m": {"failed": True, "msg": "secret detail"}})
    with pytest.raises(context_module.ModuleFailed) as raised:
        make_ctx(tmp_path, ansible=ansible).aws_module("m")
    assert str(raised.value) == "m"


def test_aws_module_returns_the_result_when_expected_keys_are_present(tmp_path):
    result = {"account": "1", "arn": "a"}
    ansible = FakeAnsibleHost({"m": result})
    ctx = make_ctx(tmp_path, ansible=ansible)
    assert ctx.aws_module("m", expect=("account", "arn")) == result


def test_ssm_lookup_raises_when_the_lookup_task_failed(tmp_path):
    msg = "Task failed: Finalization of task args for 'debug' failed: Couldn't connect to AWS"
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"msg": msg}})
    with pytest.raises(context_module.ModuleFailed) as raised:
        make_ctx(tmp_path, ansible=ansible).ssm_lookup("/a/b")
    assert str(raised.value) == "amazon.aws.ssm_parameter"


def test_ssm_lookup_raises_when_the_result_says_failed(tmp_path):
    ansible = FakeAnsibleHost({"ansible.builtin.debug": {"failed": True}})
    with pytest.raises(context_module.ModuleFailed):
        make_ctx(tmp_path, ansible=ansible).ssm_lookup("/a/b")
