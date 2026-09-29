# tests/test_graph.py
import pytest
import sample_checks as sc
from conftest import write

from preflight import Requirement, check, ok, outcome
from preflight.check import REGISTRY
from preflight.contract import load_contract
from preflight.gate import Gate
from preflight.graph import GraphError, _Builder, build_plan
from preflight.outcome import Status

CONTRACT = """
schema_version = 1
environment = "dev"

[identities.admin]
profile = "sandbox"
region = "us-east-1"
account_id = { tfvars = "envs/dev.tfvars", key = "account_id", placeholder = ["000000000000"] }
permission_set = "AWSAdministratorAccess"

[things]
items = ["a"]

[dns]
identity = "admin"
name_servers = { ssm = "/platform/dns/name_servers", identity = "admin", format = "json", how = "rerun bootstrap" }

[dns.remedy]
do = "Rerun bootstrap for {environment}."
"""  # noqa: E501


def contract(repo, text=CONTRACT, account="111111111111"):
    write(repo, "envs/dev.tfvars", f'account_id = "{account}"\n')
    return load_contract(write(repo, "preflight/contracts/dev.toml", text))


def plan(gates, c, *, link=True, strict=False):
    return build_plan(gates, {c.scope: c}, link_gates=link, strict=strict)


def test_sessions_and_ssm_parameters_become_prerequisites(repo):
    p = plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo))
    assert [n.id for n in p.ordered()] == [
        "aws.session[admin]",
        "ssm.present[/platform/dns/name_servers]",
        "graph.aws_thing[dns]",
    ]
    assert p.nodes["graph.aws_thing[dns]"].requires == [
        "aws.session[admin]",
        "ssm.present[/platform/dns/name_servers]",
    ]
    assert p.nodes["ssm.present[/platform/dns/name_servers]"].data == {
        "identity": "admin",
        "name": "/platform/dns/name_servers",
        "how": "rerun bootstrap",
    }
    assert p.nodes["aws.session[admin]"].gates == ["g"]


def test_an_identity_placeholder_blocks_its_session(repo):
    p = plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, account="000000000000"))
    placeholder_id = "contract.placeholder[identities.admin.account_id]"
    assert p.nodes["aws.session[admin]"].requires == [placeholder_id]
    placeholder = p.nodes[placeholder_id]
    assert placeholder.static.status is Status.FAIL
    assert placeholder.static.items[0].next_step.do == "Fill `account_id` in `envs/dev.tfvars`."
    assert p.ordered()[0].id == placeholder_id


def test_missing_sections_and_bad_fields_are_reported_together(repo):
    text = CONTRACT.replace('items = ["a"]', "items = [1]")
    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[sc.thing("things"), sc.thing("nope")])], contract(repo, text))
    assert caught.value.problems == [
        "[things] for graph.thing: items.0: Input should be a valid string",
        "g: graph.thing[nope]: no section [nope] in dev.toml",
    ]


def test_remedy_templates_are_checked(repo):
    text = CONTRACT.replace("{environment}", "{nope}")
    with pytest.raises(GraphError, match=r"\[dns.remedy\].do: malformed or unknown template field"):
        plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, text))


def test_remedy_templates_cannot_use_ssm_values(repo):
    text = CONTRACT.replace("{environment}", "{name_servers}")
    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, text))
    assert caught.value.problems == [
        "[dns.remedy].do: {name_servers} is not available in remedies (placeholder or ssm value)"
    ]


def test_remedy_templates_cannot_use_placeholder_values(repo):
    text = CONTRACT + (
        '\n[ph]\nname = { tfvars = "envs/dev.tfvars", key = "account_id", '
        'placeholder = ["000000000000"] }\n\n[ph.remedy]\npaste = "echo {name}"\n'
    )
    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[sc.thing("ph")])], contract(repo, text, account="000000000000"))
    assert caught.value.problems == [
        "[ph.remedy].paste: {name} is not available in remedies (placeholder or ssm value)"
    ]


def test_strict_mode_refuses_unused_sections_and_keys(repo):
    text = CONTRACT.replace('items = ["a"]', 'items = ["a"]\nstray = 1')
    gates = [Gate("g", checks=[sc.thing("things")])]
    plan(gates, contract(repo, text))
    with pytest.raises(GraphError) as caught:
        plan(gates, contract(repo, text), strict=True)
    assert caught.value.problems == [
        "[things].stray is used by no check bound to [things]",
        "[dns] in dev.toml is used by no gate",
    ]


def test_linked_gates_make_required_instances_prerequisites(repo):
    c = contract(repo)
    a = Gate("a", checks=[sc.thing("things")])
    b = Gate("b", checks=[sc.aws_thing("dns")], requires=["a"])
    linked = plan([a, b], c)
    assert "graph.thing[things]" in linked.nodes["graph.aws_thing[dns]"].requires
    assert "graph.thing[things]" in linked.nodes["aws.session[admin]"].requires
    unlinked = plan([a, b], c, link=False)
    assert "graph.thing[things]" not in unlinked.nodes["graph.aws_thing[dns]"].requires


def test_an_instance_in_two_gates_is_one_node(repo):
    gates = [Gate("a", checks=[sc.thing("things")]), Gate("b", checks=[sc.thing("things")])]
    p = plan(gates, contract(repo), link=False)
    assert p.nodes["graph.thing[things]"].gates == ["a", "b"]


def test_repository_nodes_are_prefixed_in_an_environment_plan(repo):
    env = contract(repo)
    repository = load_contract(
        write(
            repo,
            "preflight/contracts/repo.toml",
            'schema_version = 1\nscope = "repository"\n\n[things]\nitems = []\n',
        )
    )
    gates = [
        Gate("r", checks=[sc.thing("things")], scope="repository"),
        Gate("e", checks=[sc.thing("things")], requires=["r"]),
    ]
    p = build_plan(
        gates, {"environment": env, "repository": repository}, link_gates=True, strict=False
    )
    assert p.nodes["graph.thing[things]"].requires == ["repository/graph.thing[things]"]


def test_joining_a_gate_joins_its_prerequisites(repo):
    c = contract(repo, account="111111111111")
    x = Gate("x", checks=[sc.aws_thing("dns")])
    y = Gate("y", checks=[sc.aws_thing("dns")])
    z = Gate("z", checks=[REGISTRY["aws.session"]("admin")], requires=["y"])
    p = plan([x, y, z], c)
    ids = [n.id for n in p.nodes_of("y")]
    assert "aws.session[admin]" in ids
    assert "ssm.present[/platform/dns/name_servers]" in ids
    assert p.nodes["aws.session[admin]"].gates == ["x", "y", "z"]


def test_a_real_prerequisite_cycle_is_reported(repo):
    c = contract(repo)
    builder = _Builder({"environment": c})
    for name, other in (("a", "b"), ("b", "a")):
        builder._new(
            name,
            check_id="x",
            key=name,
            gates=["g"],
            contract=c,
            data={},
            identity=None,
            requires=[other],
        )
    builder.topological()
    assert builder.problems and "prerequisite cycle" in builder.problems[0]


@pytest.mark.parametrize("template", ["{environment.nope}", "{environment[x]}", "{"])
def test_malformed_remedy_templates_are_graph_errors(repo, template):
    text = CONTRACT.replace("Rerun bootstrap for {environment}.", template)
    with pytest.raises(GraphError, match="malformed or unknown template field"):
        plan([Gate("g", checks=[sc.aws_thing("dns")])], contract(repo, text))


def test_a_requirement_on_an_unknown_check_is_a_graph_error(repo):
    @check("graph.badreq", section=sc.Things, requires=[Requirement("nope.check", "identity")])
    def badreq(ctx, s):
        return outcome(ok())

    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[badreq("things")])], contract(repo))
    assert "graph.badreq: requires unknown identity check nope.check" in caught.value.problems


def test_an_unknown_identity_in_a_required_field_is_a_graph_error(repo):
    text = CONTRACT + '\n[src]\nsource = "ghost"\n'
    with pytest.raises(GraphError) as caught:
        plan([Gate("g", checks=[sc.sourced("src")])], contract(repo, text))
    assert "[src].source: no [identities.ghost] in dev.toml" in caught.value.problems


def test_an_identity_field_alone_requires_its_session(repo):
    text = CONTRACT + '\n[ident]\nidentity = "admin"\n'
    p = plan([Gate("g", checks=[sc.identified("ident")])], contract(repo, text))
    assert p.nodes["graph.identified[ident]"].requires == ["aws.session[admin]"]
