import json
import shlex

import pytest
from fakes import FakeHost, FakeResult, make_probe, observe

from preflight.catalog import github
from preflight.check import CheckCallError
from preflight.outcome import Status

NOT_FOUND = FakeResult(1, "", "gh: Not Found (HTTP 404)")


def gh(*rules):
    return FakeHost(
        [
            (pattern, body if isinstance(body, FakeResult) else FakeResult(0, json.dumps(body)))
            for pattern, body in rules
        ]
    )


def run(bound, tmp_path, host):
    return observe(bound, make_probe(tmp_path, host=host))


def test_auth(tmp_path):
    bound = github.auth()
    ok_host = FakeHost([("gh auth status", FakeResult(0))])
    assert run(bound, tmp_path, ok_host).status is Status.OK
    bad_host = FakeHost([("gh auth status", FakeResult(1))])
    item = run(bound, tmp_path, bad_host).items[0]
    assert (item.status, item.next_step.paste, item.error_type) == (
        Status.ERROR,
        "gh auth login",
        "GhAuth",
    )


def test_repo_and_actions_access(tmp_path):
    bound = github.repo(repo="tellabsadmin/iac", actions_access="organization")
    host = gh(
        ("actions/permissions/access", {"access_level": "none"}),
        ("repos/tellabsadmin/iac", {"id": 1}),
    )
    items = {i.key: i for i in run(bound, tmp_path, host).items}
    assert items["exists"].status is Status.OK
    assert items["actions_access"].status is Status.FAIL
    gone = gh(("repos/tellabsadmin/iac", NOT_FOUND))
    missing = run(bound, tmp_path, gone).items[0]
    assert missing.status is Status.FAIL
    assert missing.next_step.paste == "gh repo create tellabsadmin/iac --private"


def test_variables(tmp_path):
    bound = github.org_variables(
        org="tellabsadmin",
        variables={"AWS_REGION": "us-east-1", "AWS_ACCOUNT_ID_DEV": "111111111111"},
    )
    host = gh(
        ("variables/AWS_REGION", {"value": "us-east-1"}),
        ("variables/AWS_ACCOUNT_ID_DEV", NOT_FOUND),
    )
    items = {i.key: i for i in run(bound, tmp_path, host).items}
    assert items["AWS_REGION"].status is Status.OK
    assert items["AWS_ACCOUNT_ID_DEV"].next_step.paste == (
        "gh variable set AWS_ACCOUNT_ID_DEV --org tellabsadmin --body 111111111111"
    )


def test_variables_need_an_owner():
    with pytest.raises(CheckCallError, match="missing a required argument: 'repo'"):
        github.variables(variables={})


def test_ruleset_required_checks(tmp_path):
    bound = github.ruleset(
        repo="tellabsadmin/iac",
        name="main",
        required_checks=["validate", "plans", "bootstrap_applied"],
    )
    detail = {
        "enforcement": "active",
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "validate"}, {"context": "plans"}]
                },
            }
        ],
    }
    host = gh(
        ("rulesets/7", detail),
        ("rulesets", [[{"id": 1, "name": "other"}], [{"id": 7, "name": "main"}]]),
    )
    items = {i.key: i for i in run(bound, tmp_path, host).items}
    assert items["enforcement"].status is Status.OK
    assert items["required_checks"].status is Status.FAIL
    assert "bootstrap_applied" in items["required_checks"].next_step.do


def test_secret_names_never_read_values(tmp_path):
    bound = github.secret_names(
        repo="tellabsadmin/iac", environment="platform-dev", names=["SOPS_AGE_KEY"]
    )
    host = gh(("environments/platform-dev/secrets", [{"secrets": [{"name": "OTHER"}]}]))
    item = run(bound, tmp_path, host).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste.startswith("gh secret set SOPS_AGE_KEY --env platform-dev")


def test_workflow_green(tmp_path):
    bound = github.workflow_green(repo="tellabsadmin/iac", workflow="platform.yml", branch="main")
    runs = {"workflow_runs": [{"conclusion": "failure", "html_url": "https://x/1"}]}
    failed = gh(("platform.yml/runs", runs))
    item = run(bound, tmp_path, failed).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "https://x/1")
    runs = {"workflow_runs": [{"conclusion": "success", "html_url": "u"}]}
    green = gh(("platform.yml/runs", runs))
    assert run(bound, tmp_path, green).status is Status.OK
    assert "branch=main" in green.commands[0] and "status=completed" in green.commands[0]


def test_other_gh_failures_are_errors(tmp_path):
    bound = github.environments(repo="tellabsadmin/iac", environments=["platform-dev"])
    host = gh(("environments/platform-dev", FakeResult(4, "", "gh: authentication required")))
    item = run(bound, tmp_path, host).items[0]
    assert (item.status, item.next_step.paste) == (Status.ERROR, "gh auth login")


def test_ruleset_found_on_second_page(tmp_path):
    bound = github.ruleset(repo="tellabsadmin/iac", name="main")
    host = gh(
        ("rulesets/7", {"enforcement": "active"}),
        ("rulesets", [[{"id": 1, "name": "other"}], [{"id": 7, "name": "main"}]]),
    )
    assert run(bound, tmp_path, host).status is Status.OK
    assert "--paginate --slurp" in host.commands[0]


def test_secret_on_second_page_is_found_and_paste_is_runnable(tmp_path):
    pages = [{"secrets": [{"name": "A"}]}, {"secrets": [{"name": "B"}]}]
    bound = github.secret_names(repo="tellabsadmin/iac", names=["B", "C"])
    items = run(bound, tmp_path, gh(("actions/secrets", pages))).items
    assert items[0].status is Status.OK
    assert items[1].next_step.paste == "gh secret set C --repo tellabsadmin/iac"
    assert items[1].next_step.do == "Set the secret C."


def test_missing_secret_list_404_mentions_token_visibility(tmp_path):
    bound = github.secret_names(repo="tellabsadmin/iac", names=["A"])
    item = run(bound, tmp_path, gh(("actions/secrets", NOT_FOUND))).items[0]
    assert item.next_step.do.endswith(", or check that your gh token can see it.")


def test_repo_404_mentions_token_visibility(tmp_path):
    bound = github.repo(repo="tellabsadmin/iac")
    item = run(bound, tmp_path, gh(("repos/", NOT_FOUND))).items[0]
    assert item.next_step.do.endswith(", or check that your gh token can see it.")


def test_variable_value_with_quote_round_trips_through_shlex(tmp_path):
    value = "it's a 'value'; rm -rf /"
    bound = github.variables(repo="tellabsadmin/iac", variables={"GREETING": value})
    item = run(bound, tmp_path, gh(("variables/GREETING", NOT_FOUND))).items[0]
    assert shlex.split(item.next_step.paste) == [
        "gh",
        "variable",
        "set",
        "GREETING",
        "--repo",
        "tellabsadmin/iac",
        "--body",
        value,
    ]


@pytest.mark.parametrize(
    "build",
    [
        lambda: github.variables(repo="a/b", variables={"bad name; x": "1"}),
        lambda: github.secret_names(repo="a/b", names=["1BAD"]),
        lambda: github.secret_names(repo="a/b", names=["OK"], environment="x y"),
        lambda: github.environments(repo="a/b", environments=["a;b"]),
        lambda: github.environments(repo="a/b", environments=["dev", "dev"]),
        lambda: github.secret_names(repo="a/b", names=["TOKEN", "TOKEN"]),
    ],
)
def test_names_are_validated(build):
    with pytest.raises(CheckCallError):
        build()


@pytest.mark.parametrize(
    ("result", "error_type", "paste", "do_start"),
    [
        (FakeResult(127, "", "gh: not found"), "MissingTool", None, "Install the GitHub CLI"),
        (FakeResult(1, "", "gh: HTTP 401: Bad credentials"), "GhAuth", "gh auth login", "Sign in"),
        (
            FakeResult(4, "", "To get started with GitHub CLI, please run: gh auth login"),
            "GhAuth",
            "gh auth login",
            "Sign in",
        ),
        (FakeResult(1, "", "HTTP 403: Forbidden"), "GhRefused", None, "GitHub refused"),
        (FakeResult(1, "", "API rate limit exceeded"), "GhRefused", None, "GitHub refused"),
        (FakeResult(1, "", "HTTP 429"), "GhRefused", None, "GitHub refused"),
        (FakeResult(1, "", "dial tcp: timeout"), "GhError", None, "Could not reach GitHub"),
        (FakeResult(0, "not json"), "GhError", None, "Could not reach GitHub"),
    ],
)
def test_gh_failure_kinds(tmp_path, result, error_type, paste, do_start):
    bound = github.environments(repo="a/b", environments=["dev"])
    item = run(bound, tmp_path, gh(("environments/dev", result))).items[0]
    assert item.status is Status.ERROR
    assert item.error_type == error_type
    assert item.next_step.paste == paste
    assert item.next_step.do.startswith(do_start)


def test_unexpected_shapes_do_not_crash(tmp_path):
    bound = github.ruleset(repo="a/b", name="main")
    item = run(bound, tmp_path, gh(("rulesets", {"not": "a list"}))).items[0]
    assert item.status is Status.ERROR
    workflow = github.workflow_green(repo="a/b", workflow="w.yml")
    item = run(workflow, tmp_path, gh(("runs", [1]))).items[0]
    assert item.status is Status.ERROR
