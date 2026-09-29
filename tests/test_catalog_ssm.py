import json
import re

from fakes import FakeAnsibleHost, make_ctx
from testinfra.modules.ansible import AnsibleException

from preflight.catalog import ssm
from preflight.outcome import Status

NAME = re.compile(r"ssm_parameter', '([^']+)'")


def store(values):
    def answer(args):
        value = values[NAME.search(args["msg"])[1]]
        if isinstance(value, BaseException):
            raise value
        return {"msg": value}

    return FakeAnsibleHost({"ansible.builtin.debug": answer})


def test_present(tmp_path):
    section = ssm.PresentSection(
        identity="admin", name="/platform/state/bucket", how="rerun bootstrap"
    )
    ok_ctx = make_ctx(tmp_path, ansible=store({"/platform/state/bucket": "tellabs-tfstate-dev"}))
    assert ssm.present.observe(ok_ctx, section).status is Status.OK
    missing = make_ctx(tmp_path, ansible=store({"/platform/state/bucket": ""}))
    item = ssm.present.observe(missing, section).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.do == "SSM parameter /platform/state/bucket is missing or empty."
    assert item.next_step.paste == "rerun bootstrap"
    assert item.next_step.generic is True
    assert item.observed is None


def test_parameters_reports_each_name(tmp_path):
    section = ssm.ParametersSection(identity="admin", names=["/a", "/b", "/c"])
    ctx = make_ctx(
        tmp_path,
        ansible=store({"/a": "1", "/b": "None", "/c": AnsibleException({"failed": True})}),
    )
    items = {i.key: i for i in ssm.parameters.observe(ctx, section).items}
    assert items["/a"].status is Status.OK
    assert items["/b"].status is Status.FAIL
    assert (items["/c"].status, items["/c"].error_type) == (Status.ERROR, "AnsibleException")


TASK_FAILED = (
    "Task failed: Finalization of task args for 'ansible.builtin.debug' failed: "
    "Couldn't connect to AWS: SECRET"
)


def test_lookup_failure_message_is_an_error_and_not_leaked(tmp_path):
    ctx = make_ctx(tmp_path, ansible=store({"/a": TASK_FAILED}))
    outcome = ssm.parameters.observe(ctx, ssm.ParametersSection(identity="admin", names=["/a"]))
    item = outcome.items[0]
    assert (item.status, item.error_type) == (Status.ERROR, "ModuleFailed")
    assert "SECRET" not in json.dumps(item.to_dict())
    single = ssm.present.observe(ctx, ssm.PresentSection(identity="admin", name="/a")).items[0]
    assert (single.status, single.error_type) == (Status.ERROR, "ModuleFailed")
