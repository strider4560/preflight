import json
import re

import pytest
from fakes import IDENTITY, FakeAnsibleHost, make_probe, observe
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import ssm
from preflight.check import CheckCallError
from preflight.outcome import Status

NAME = re.compile(r"ssm_parameter', '([^']+)'")


def store(values):
    def answer(args):
        value = values[NAME.search(args["msg"])[1]]
        if isinstance(value, BaseException):
            raise value
        return {"msg": value}

    return FakeAnsibleHost({"ansible.builtin.debug": answer})


def test_each_parameter_is_its_own_item(tmp_path):
    probe = make_probe(
        tmp_path,
        ansible=store({"/a": "1", "/b": "None", "/c": AnsibleException({"failed": True})}),
    )
    bound = ssm.parameters_exist(names=["/a", "/b", "/c"], identity=IDENTITY)
    items = {i.key: i for i in observe(bound, probe).items}
    assert items["/a"].status is Status.OK
    assert (items["/c"].status, items["/c"].error_type) == (Status.ERROR, "AnsibleException")
    missing = items["/b"]
    assert missing.status is Status.FAIL
    assert missing.next_step.do == (
        "SSM parameter /b does not exist in account 111111111111 (us-east-1), or is empty. "
        "Publish it from the stack that owns it, then confirm:"
    )
    assert missing.next_step.paste == (
        "aws ssm get-parameter --name /b --profile sandbox --region us-east-1 "
        "--query Parameter.Name --output text"
    )
    assert missing.observed is None


TASK_FAILED = (
    "Task failed: Finalization of task args for 'ansible.builtin.debug' failed: "
    "Couldn't connect to AWS: SECRET"
)


def test_lookup_failure_message_is_an_error_and_not_leaked(tmp_path):
    probe = make_probe(tmp_path, ansible=store({"/a": TASK_FAILED}))
    result = observe(ssm.parameters_exist(names=["/a"], identity=IDENTITY), probe)
    assert result.items[0].status is Status.ERROR
    assert "SECRET" not in json.dumps(result.to_dict())


def test_names_are_unique_valid_and_not_empty():
    with pytest.raises(CheckCallError, match="duplicate entries: /a"):
        ssm.parameters_exist(names=["/a", "/b", "/a"], identity=IDENTITY)
    with pytest.raises(CheckCallError, match="names"):
        ssm.parameters_exist(names=[], identity=IDENTITY)
    with pytest.raises(CheckCallError, match="names.0"):
        ssm.parameters_exist(names=["no-slash"], identity=IDENTITY)
