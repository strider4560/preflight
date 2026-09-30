from preflight.outcome import Item, Status, error, fail, ok, outcome, pending
from preflight.render import NO_NEXT_STEP, CheckResult, GuardResult, RunResult, worklist

ASSUMED = CheckResult(
    "aws.assumed",
    outcome(
        fail(
            do="Your shell acts as account 5035; the next command needs 7113.",
            paste="export AWS_PROFILE=sandbox",
        )
    ),
)


def test_a_stopped_run_lists_passed_guards_the_open_steps_and_what_did_not_run():
    result = RunResult(
        "bootstrap_entry",
        ("dev",),
        exit_code=1,
        guards=(
            GuardResult("IDs filled in", (CheckResult("iac.filled", outcome(ok("account_id"))),)),
            GuardResult(
                "this shell is the account's administrator",
                (ASSUMED, CheckResult("aws.region", outcome(ok()))),
            ),
        ),
        not_run=("the checkout holds the latest main",),
    )
    assert worklist(result) == (
        "preflight bootstrap_entry dev\n"
        "\n"
        "  ✓ IDs filled in\n"
        "  ✗ this shell is the account's administrator\n"
        "      FAIL    aws.assumed\n"
        "              Your shell acts as account 5035; the next command needs 7113.\n"
        "                export AWS_PROFILE=sandbox\n"
        "      ok      aws.region\n"
        "\n"
        "Stopped at: this shell is the account's administrator\n"
        "Not run: the checkout holds the latest main\n"
    )


def test_item_keys_extend_labels_and_identical_steps_merge():
    step = {"do": "Fill it.", "paste": "gh api orgs/x --jq .id"}
    guard = GuardResult(
        "IDs filled in",
        (
            CheckResult(
                "iac.filled(common.tfvars)",
                outcome(fail("github_org_id", **step), fail("iac_repo_id", **step)),
            ),
        ),
    )
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert (
        "      FAIL    iac.filled(common.tfvars):github_org_id, "
        "iac.filled(common.tfvars):iac_repo_id\n"
        "              Fill it.\n"
        "                gh api orgs/x --jq .id\n"
    ) in text


def test_unmet_items_show_under_the_guard_with_their_provider():
    guard = GuardResult(
        "bootstrap published",
        unmet=(error(do="Sign in to profile sandbox.", paste="aws sso login --profile sandbox"),),
        unmet_by="admin",
    )
    text = worklist(RunResult("bootstrap", ("dev",), exit_code=1, guards=(guard,)))
    assert "      ERROR   needs admin\n              Sign in to profile sandbox.\n" in text


def test_an_error_names_its_type_after_the_labels():
    step = {"do": "Install the GitHub CLI (gh).", "error_type": "MissingTool"}
    guard = GuardResult(
        "repository ready",
        (
            CheckResult("github.variables", outcome(error(**step))),
            CheckResult("github.secrets", outcome(error(**step))),
        ),
    )
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert (
        "      ERROR   github.variables, github.secrets (MissingTool)\n"
        "              Install the GitHub CLI (gh).\n"
    ) in text


def test_wait_ref_and_warnings():
    guard = GuardResult(
        "delegated",
        (
            CheckResult(
                "dns.cname",
                outcome(
                    fail("a.dev", do="Add the CNAME.", wait="up to an hour", ref="README"),
                    fail("b.dev", do="Add the other.", advisory=True),
                ),
            ),
        ),
    )
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert "              wait: up to an hour\n              see: README\n" in text
    assert "Warnings:\n  ! dns.cname:b.dev  Add the other.\n" in text


def test_passing_validated_and_problem_footers():
    passed = RunResult("g", guards=(GuardResult("one", (CheckResult("c", outcome(ok())),)),))
    assert worklist(passed).endswith("  ✓ one\n\nEvery guard passed.\n")
    validated = RunResult("g", ("dev", "--validate"), validated=True, guards=passed.guards)
    assert worklist(validated).endswith("Validated every guard; nothing was observed.\n")
    broken = RunResult("g", exit_code=2, problems=("provider admin raised KeyError",))
    assert worklist(broken) == "preflight g\n\n\nProblems:\n  - provider admin raised KeyError\n"


def test_stopped_at_is_the_first_guard_that_did_not_pass():
    good = GuardResult("one", (CheckResult("c", outcome(ok())),))
    bad = GuardResult("two", (CheckResult("c", outcome(fail(do="x"))),))
    assert RunResult("g", guards=(good, bad)).stopped_at == "two"
    assert RunResult("g", guards=(good,)).stopped_at is None


def test_multi_line_do_indents_every_line():
    guard = GuardResult("g1", (CheckResult("c", outcome(fail(do="First line.\nSecond line."))),))
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert f"\n{' ' * 14}First line.\n{' ' * 14}Second line.\n" in text


def test_pending_item_shows_status_column_and_wait():
    guard = GuardResult("g1", (CheckResult("cache", outcome(pending(wait="5 minutes"))),))
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert "      pending cache\n" in text
    assert "              wait: 5 minutes\n" in text


def test_item_without_next_step_renders_the_placeholder():
    guard = GuardResult("g1", (CheckResult("c", outcome(Item("k", Status.FAIL))),))
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert f"      FAIL    c:k\n              {NO_NEXT_STEP.do}\n" in text


def test_unmet_items_come_before_check_items():
    guard = GuardResult(
        "g1",
        (CheckResult("c", outcome(fail(do="Check step."))),),
        unmet=(error(do="Unmet step."),),
        unmet_by="admin",
    )
    labels = [label for label, _ in guard.labelled_items()]
    assert labels == ["needs admin", "c"]
    text = worklist(RunResult("g", exit_code=1, guards=(guard,)))
    assert text.index("needs admin") < text.index("      FAIL    c")


def test_advisory_only_guard_passes_and_warns():
    guard = GuardResult(
        "g1", (CheckResult("c", outcome(fail(do="Consider this.", advisory=True))),)
    )
    assert guard.passed
    text = worklist(RunResult("g", guards=(guard,)))
    assert "  ✓ g1\n" in text
    assert "Warnings:\n  ! c  Consider this.\n" in text


def test_no_guards_and_exit_zero_passes():
    assert worklist(RunResult("g")) == "preflight g\n\n\nEvery guard passed.\n"
