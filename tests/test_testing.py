from typing import Annotated, Literal

import pytest

from preflight.catalog import aws, ssm
from preflight.gate import Gate
from preflight.outcome import fail, ok, outcome
from preflight.params import Arg, Depends
from preflight.testing import GateClient, MissingStandIn

Env = Annotated[Literal["dev", "prod"], Arg()]
IDENTITY = aws.Identity(
    profile="sandbox",
    region="us-east-1",
    account_id="111111111111",
    permission_set="AWSAdministratorAccess",
)


def admin(env: Env):
    with aws.signed_in(**IDENTITY.model_dump(exclude_none=True)) as identity:
        yield identity


Admin = Annotated[aws.Identity, Depends(admin)]

gate = Gate("bootstrap")


@gate.guard("bootstrap published")
def published(identity: Admin):
    return [ssm.parameters_exist(names=["/platform/state/bucket"], identity=identity)]


def test_stand_ins_answer_by_check_including_the_providers_session(tmp_path):
    missing = outcome(fail("/platform/state/bucket", do="Publish it."))
    client = GateClient(
        gate, {aws.session: outcome(ok()), ssm.parameters_exist: missing}, root=tmp_path
    )
    result = client.run(["dev"])
    assert (result.exit_code, result.stopped_at) == (1, "bootstrap published")
    assert "Publish it." in result.output


def test_dependency_overrides_replace_providers(tmp_path):
    gate.dependency_overrides[admin] = lambda: IDENTITY
    try:
        client = GateClient(gate, {ssm.parameters_exist: outcome(ok("x"))}, root=tmp_path)
        assert client.run().exit_code == 0
    finally:
        gate.dependency_overrides.clear()


def test_a_check_without_a_stand_in_fails_the_test(tmp_path):
    with pytest.raises(MissingStandIn, match="aws.session"):
        GateClient(gate, {}, root=tmp_path).run(["dev"])


def test_callables_can_answer_from_the_bound_arguments(tmp_path):
    def answer(bound):
        return outcome(*(ok(name) for name in bound.values["names"]))

    client = GateClient(
        gate, {aws.session: outcome(ok()), ssm.parameters_exist: answer}, root=tmp_path
    )
    assert client.run(["dev"]).exit_code == 0
