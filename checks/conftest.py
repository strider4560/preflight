"""Shared fixtures for operational checks. Add module-local fixtures as needed."""

import pytest

from gate.aws import AwsContexts
from gate.plugin import CONTRACT


@pytest.fixture(scope="session")
def aws(contract):
    return AwsContexts(contract)


def pytest_generate_tests(metafunc):
    contract = metafunc.config.stash[CONTRACT]
    if "identity_name" in metafunc.fixturenames:
        names = sorted(contract.identities)
        metafunc.parametrize("identity_name", names, ids=names)
    if "subnet_requirement" in metafunc.fixturenames:
        subnets = contract.network.subnets if contract.network else []
        metafunc.parametrize("subnet_requirement", subnets, ids=[s.name for s in subnets])
    if "runtime_probe" in metafunc.fixturenames:
        probes = contract.runtime.probes if contract.runtime else []
        metafunc.parametrize("runtime_probe", probes, ids=[p.name for p in probes])
