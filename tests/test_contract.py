import os

import pytest
from conftest import write

from preflight.contract import ContractError, load_contract
from preflight.resolvers import LazySsm

TFVARS = 'account_id = "111111111111"\nroot_domain = "tellabs.dev"\nzones = ["app"]\n'
CONTRACT = """
schema_version = 1
environment = "dev"

[identities.admin]
profile = "sandbox"
region = "us-east-1"
account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID" }
permission_set = "AWSAdministratorAccess"

[delegation]
root = { tfvars = "envs/dev.tfvars", key = "root_domain" }
zones = { tfvars = "envs/dev.tfvars", key = "zones", placeholder = ["CHANGEME"] }
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json" }

[delegation.remedy]
ref = "README, DNS step 1"
"""  # noqa: E501


def setup(repo, tfvars=TFVARS, contract=CONTRACT):
    write(repo, "envs/dev.tfvars", tfvars)
    return write(repo, "preflight/contracts/dev.toml", contract)


def test_loads_and_resolves(repo):
    contract = load_contract(setup(repo))
    assert (contract.scope, contract.environment, contract.root) == ("environment", "dev", repo)
    assert contract.identity("admin").account_id == "111111111111"
    delegation = contract.sections["delegation"]
    assert delegation["root"] == "tellabs.dev"
    assert delegation["zones"] == ["app"]
    assert isinstance(delegation["name_servers"], LazySsm)
    assert delegation["remedy"] == {"ref": "README, DNS step 1"}
    assert contract.placeholders == []
    assert set(contract.inputs) == {"preflight/contracts/dev.toml", "envs/dev.tfvars"}
    assert contract.lazy_fields("delegation") == {"name_servers"}
    assert contract.template_values("delegation") == {"root": "tellabs.dev", "environment": "dev"}


def test_placeholders_are_recorded_not_rejected(repo):
    tfvars = (
        'account_id = "000000000000"\nroot_domain = "tellabs.dev"\nzones = ["app", "CHANGEME"]\n'
    )
    contract = load_contract(setup(repo, tfvars=tfvars))
    assert [(p.path, p.owner, p.field) for p in contract.placeholders] == [
        ("identities.admin.account_id", "identities.admin", "account_id"),
        ("delegation.zones[1]", "delegation", "zones"),
    ]
    first = contract.placeholders[0]
    assert first.id == "contract.placeholder[identities.admin.account_id]"
    assert first.next_step().do == "Fill `account_id` in `envs/dev.tfvars`."
    assert first.next_step().paste == "the Sandbox account ID"
    assert contract.ready_identities() == {}


def test_every_problem_is_reported_together(repo):
    bad = (
        CONTRACT.replace("schema_version = 1", "schema_version = 2")
        .replace('key = "root_domain"', 'key = "nope"')
        .replace(
            'permission_set = "AWSAdministratorAccess"',
            'permission_set = "AWSAdministratorAccess"\nrole = "x"',
        )
    )
    with pytest.raises(ContractError) as caught:
        load_contract(setup(repo, contract=bad))
    text = "\n".join(caught.value.problems)
    assert "schema_version must be 1" in text
    assert "has no variable nope" in text
    assert "exactly one of role or permission_set" in text


def test_a_toml_syntax_error_names_the_line(repo):
    with pytest.raises(ContractError) as caught:
        load_contract(setup(repo, contract="schema_version = \n"))
    assert "not valid TOML" in caught.value.problems[0]
    assert "line 1" in caught.value.problems[0]


def test_a_contract_outside_a_git_work_tree_is_refused(tmp_path):
    path = write(tmp_path, "c.toml", 'schema_version = 1\nenvironment = "dev"\n')
    with pytest.raises(ContractError, match="not inside a git work tree"):
        load_contract(path)


def test_an_identity_cannot_use_ssm(repo):
    bad = CONTRACT.replace(
        'account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"], how = "the Sandbox account ID" }',  # noqa: E501
        'account_id = { ssm = "/x", identity = "admin" }',
    )
    with pytest.raises(ContractError, match="cannot use an ssm reference"):
        load_contract(setup(repo, contract=bad))


def test_a_repository_contract_has_no_environment(repo):
    with pytest.raises(ContractError, match="has no environment"):
        load_contract(
            setup(repo, contract='schema_version = 1\nscope = "repository"\nenvironment = "dev"\n')
        )


def test_top_level_scalars_other_than_the_known_keys_are_refused(repo):
    with pytest.raises(ContractError, match="unknown top-level key"):
        load_contract(setup(repo, contract='schema_version = 1\nenvironment = "dev"\nextra = 1\n'))


def test_changed_inputs(repo):
    contract = load_contract(setup(repo))
    (repo / "envs/dev.tfvars").write_text(TFVARS + 'extra = "1"\n')
    assert contract.changed_inputs() == ["envs/dev.tfvars"]


def test_a_malformed_glob_is_a_contract_error(repo):
    bad = 'schema_version = 1\nenvironment = "dev"\n[s]\nx = { yaml_glob = "a/**b/*.yaml" }\n'
    with pytest.raises(ContractError):
        load_contract(setup(repo, contract=bad))


def test_a_nul_in_a_path_is_a_contract_error(repo):
    bad = 'schema_version = 1\nenvironment = "dev"\n[s]\nx = { yaml = "a\\u0000b" }\n'
    with pytest.raises(ContractError):
        load_contract(setup(repo, contract=bad))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_an_unreadable_referenced_file_is_a_contract_error(repo):
    path = setup(repo)
    secret = repo / "envs/dev.tfvars"
    secret.chmod(0)
    try:
        with pytest.raises(ContractError, match="cannot be read"):
            load_contract(path)
    finally:
        secret.chmod(0o644)


def test_missing_git_is_a_contract_error(repo, monkeypatch):
    path = setup(repo)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ContractError, match="git is not available"):
        load_contract(path)


def test_a_placeholder_does_not_hide_the_identity_model_check(repo):
    tfvars = 'account_id = "000000000000"\nroot_domain = "tellabs.dev"\nzones = ["app"]\n'
    bad = CONTRACT.replace(
        'permission_set = "AWSAdministratorAccess"',
        'permission_set = "AWSAdministratorAccess"\nrole = "x"',
    )
    with pytest.raises(ContractError, match="give exactly one of role or permission_set"):
        load_contract(setup(repo, tfvars=tfvars, contract=bad))


def test_a_failed_identity_reference_is_reported_once(repo):
    bad = CONTRACT.replace('key = "account_id"', 'key = "missing"')
    with pytest.raises(ContractError) as caught:
        load_contract(setup(repo, contract=bad))
    mentions = [p for p in caught.value.problems if "account_id" in p]
    assert len(mentions) == 1
    assert "has no variable missing" in mentions[0]
