from fakes import FakeHost, FakeResult, make_ctx

from preflight.catalog import tofu
from preflight.outcome import Status

SECTION = tofu.PlanSection(dir="stacks/bootstrap", var_files=["envs/dev.tfvars"], identity="admin")


def host(init=None, plan=None, files=None):
    init = init or FakeResult(0)
    plan = plan or FakeResult(0)
    return FakeHost([(" init -input", init), (" plan -lock", plan)], files=files)


def observe(tmp_path, fake):
    return tofu.plan_clean.observe(make_ctx(tmp_path, host=fake), SECTION)


def test_a_clean_plan_is_read_only(tmp_path):
    fake = host()
    assert observe(tmp_path, fake).status is Status.OK
    init, plan = fake.commands
    assert "TF_DATA_DIR=" in init and "-lockfile=readonly" in init and "-reconfigure" in init
    assert f"-var-file={tmp_path}/envs/dev.tfvars" in init
    assert "-lock=false" in plan and "-detailed-exitcode" in plan
    assert f"-chdir={tmp_path}/stacks/bootstrap" in plan


def test_pending_changes_fail_with_a_generic_step(tmp_path):
    item = observe(tmp_path, host(plan=FakeResult(2))).items[0]
    assert (item.status, item.next_step.generic) == (Status.FAIL, True)


def test_an_error_reports_only_the_first_error_line(tmp_path):
    stderr = "│ Error: No valid credential sources found\n│ \n│ detail with SECRET\n"
    item = observe(tmp_path, host(plan=FakeResult(1, "", stderr))).items[0]
    assert item.status is Status.ERROR
    assert item.observed == "Error: No valid credential sources found"


def test_leftover_local_state_is_refused(tmp_path):
    fake = host(files={f"{tmp_path}/stacks/bootstrap/backend.tf.off": ""})
    item = observe(tmp_path, fake).items[0]
    assert item.error_type == "LeftoverState"
    assert fake.commands == []


def test_a_missing_tofu_binary(tmp_path):
    assert observe(tmp_path, host(init=FakeResult(127))).items[0].error_type == "MissingTool"


def test_first_error():
    assert tofu.first_error("x\nError: boom\n") == "Error: boom"
    assert tofu.first_error("nothing") is None
    assert len(tofu.first_error("Error: " + "a" * 500)) == 200
