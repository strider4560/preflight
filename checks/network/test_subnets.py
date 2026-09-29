"""Visibility, ownership, AZ placement and capacity; not a routing certification."""

import pytest

from gate.assertions import require

pytestmark = pytest.mark.owner("network-platform")


def test_subnet_contract(aws, contract, subnet_requirement):
    expected = contract.network
    target = subnet_requirement
    subnets = aws.client(expected.identity, "ec2").describe_subnets(SubnetIds=[target.id])[
        "Subnets"
    ]
    require(
        len(subnets) == 1,
        expected=f"exactly one visible subnet {target.id}",
        observed=f"{len(subnets)} subnets",
        remediation="Check the configured ID, region and AWS RAM sharing.",
    )
    subnet = subnets[0]
    fields = {
        "VpcId": expected.vpc_id,
        "OwnerId": expected.owner_account_id,
        "AvailabilityZoneId": target.az_id,
        "State": "available",
        "MapPublicIpOnLaunch": False,
    }
    problems = [
        f"{field}: expected {value!r}, observed {subnet.get(field)!r}"
        for field, value in fields.items()
        if subnet.get(field) != value
    ]
    required = target.additional_ipv4_required + target.reserve_ipv4
    available = subnet["AvailableIpAddressCount"]
    if available < required:
        problems.append(f"free IPv4 addresses: expected >= {required}, observed {available}")
    require(
        not problems,
        expected=f"subnet {target.name} ({target.id}) satisfies its bootstrap contract",
        observed="; ".join(problems),
        remediation="Ask the network platform owner to correct the subnet or available capacity.",
    )
