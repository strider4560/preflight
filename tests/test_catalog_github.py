import json

import pytest
from fakes import FakeHost, FakeResult, make_ctx
from pydantic import ValidationError

from preflight.catalog import github
from preflight.outcome import Status

NOT_FOUND = FakeResult(1, "", "gh: Not Found (HTTP 404)")


def gh(*rules):
    return FakeHost(
        [
            (pattern, body if isinstance(body, FakeResult) else FakeResult(0, json.dumps(body)))
            for pattern, body in rules
        ]
    )


def run(check, section, tmp_path, host):
    return check.observe(make_ctx(tmp_path, host=host), section)


def test_auth(tmp_path):
    section = github.AuthSection()
    ok_host = FakeHost([("gh auth status", FakeResult(0))])
    assert run(github.auth, section, tmp_path, ok_host).status is Status.OK
    bad_host = FakeHost([("gh auth status", FakeResult(1))])
    item = run(github.auth, section, tmp_path, bad_host).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "gh auth login")


def test_repo_and_actions_access(tmp_path):
    section = github.RepoSection(repo="tellabsadmin/iac", actions_access="organization")
    host = gh(
        ("actions/permissions/access", {"access_level": "none"}),
        ("repos/tellabsadmin/iac", {"id": 1}),
    )
    items = {i.key: i for i in run(github.repo, section, tmp_path, host).items}
    assert items["exists"].status is Status.OK
    assert items["actions_access"].status is Status.FAIL
    gone = gh(("repos/tellabsadmin/iac", NOT_FOUND))
    missing = run(github.repo, section, tmp_path, gone).items[0]
    assert missing.status is Status.FAIL
    assert missing.next_step.paste == "gh repo create tellabsadmin/iac --private"


def test_variables(tmp_path):
    section = github.VariablesSection(
        org="tellabsadmin",
        variables={"AWS_REGION": "us-east-1", "AWS_ACCOUNT_ID_DEV": "111111111111"},
    )
    host = gh(
        ("variables/AWS_REGION", {"value": "us-east-1"}),
        ("variables/AWS_ACCOUNT_ID_DEV", NOT_FOUND),
    )
    items = {i.key: i for i in run(github.variables, section, tmp_path, host).items}
    assert items["AWS_REGION"].status is Status.OK
    assert items["AWS_ACCOUNT_ID_DEV"].next_step.paste == (
        "gh variable set AWS_ACCOUNT_ID_DEV --org tellabsadmin --body '111111111111'"
    )
    with pytest.raises(ValidationError):
        github.VariablesSection(variables={})


def test_ruleset_required_checks(tmp_path):
    section = github.RulesetSection(
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
    host = gh(("rulesets/7", detail), ("rulesets", [{"id": 7, "name": "main"}]))
    items = {i.key: i for i in run(github.ruleset, section, tmp_path, host).items}
    assert items["enforcement"].status is Status.OK
    assert items["required_checks"].status is Status.FAIL
    assert "bootstrap_applied" in items["required_checks"].next_step.do


def test_secret_names_never_read_values(tmp_path):
    section = github.SecretNamesSection(
        repo="tellabsadmin/iac", environment="platform-dev", names=["SOPS_AGE_KEY"]
    )
    host = gh(("environments/platform-dev/secrets", {"secrets": [{"name": "OTHER"}]}))
    item = run(github.secret_names, section, tmp_path, host).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste.startswith("gh secret set SOPS_AGE_KEY --env platform-dev")


def test_workflow_green(tmp_path):
    section = github.WorkflowSection(
        repo="tellabsadmin/iac", workflow="platform.yml", branch="main"
    )
    runs = {"workflow_runs": [{"conclusion": "failure", "html_url": "https://x/1"}]}
    failed = gh(("platform.yml/runs", runs))
    item = run(github.workflow_green, section, tmp_path, failed).items[0]
    assert (item.status, item.next_step.paste) == (Status.FAIL, "https://x/1")
    runs = {"workflow_runs": [{"conclusion": "success", "html_url": "u"}]}
    green = gh(("platform.yml/runs", runs))
    assert run(github.workflow_green, section, tmp_path, green).status is Status.OK
    assert "branch=main" in green.commands[0] and "status=completed" in green.commands[0]


def test_other_gh_failures_are_errors(tmp_path):
    section = github.EnvironmentsSection(repo="tellabsadmin/iac", environments=["platform-dev"])
    host = gh(("environments/platform-dev", FakeResult(4, "", "gh: authentication required")))
    item = run(github.environments, section, tmp_path, host).items[0]
    assert (item.status, item.next_step.paste) == (Status.ERROR, "gh auth login")
