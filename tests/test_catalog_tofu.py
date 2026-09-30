from fakes import IDENTITY, FakeHost, FakeResult, make_probe, observe

from preflight.catalog import tofu
from preflight.outcome import Status

BOUND = tofu.plan_clean(dir="stacks/bootstrap", var_files=["envs/dev.tfvars"], identity=IDENTITY)


def host(init=None, plan=None, files=None):
    init = init or FakeResult(0)
    plan = plan or FakeResult(0)
    return FakeHost([(" init -input", init), (" plan -lock", plan)], files=files)


def run(tmp_path, fake):
    return observe(BOUND, make_probe(tmp_path, host=fake))


def test_a_clean_plan_is_read_only(tmp_path):
    fake = host()
    assert run(tmp_path, fake).status is Status.OK
    init, plan = fake.commands
    assert "TF_DATA_DIR=" in init and "-lockfile=readonly" in init and "-reconfigure" in init
    assert f"-var-file={tmp_path}/envs/dev.tfvars" in init
    assert "-lock=false" in plan and "-detailed-exitcode" in plan
    assert f"-chdir={tmp_path}/stacks/bootstrap" in plan


def test_pending_changes_fail_with_the_checks_own_step(tmp_path):
    item = run(tmp_path, host(plan=FakeResult(2))).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.do == (
        "stacks/bootstrap has changes that are not applied; apply them, then rerun this gate."
    )


def test_an_error_reports_only_the_first_error_line(tmp_path):
    stderr = "│ Error: No valid credential sources found\n│ \n│ detail with SECRET\n"
    item = run(tmp_path, host(plan=FakeResult(1, "", stderr))).items[0]
    assert item.status is Status.ERROR
    assert item.observed == "Error: No valid credential sources found"


def test_leftover_local_state_is_refused(tmp_path):
    fake = host(files={f"{tmp_path}/stacks/bootstrap/backend.tf.off": ""})
    item = run(tmp_path, fake).items[0]
    assert item.error_type == "LeftoverState"
    assert fake.commands == []


def test_a_missing_tofu_binary(tmp_path):
    assert run(tmp_path, host(init=FakeResult(127))).items[0].error_type == "MissingTool"


def test_the_label_names_the_directory_and_the_timeout_is_long():
    assert BOUND.label == "tofu.plan_clean(stacks/bootstrap)"
    assert BOUND.timeout == 600


def test_first_error():
    assert tofu.first_error("x\nError: boom\n") == "Error: boom"
    assert tofu.first_error("nothing") is None
    assert len(tofu.first_error("Error: " + "a" * 500)) == 200
