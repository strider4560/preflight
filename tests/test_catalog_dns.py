import pytest
from fakes import FakeDns, make_probe, observe

from preflight.catalog import dns
from preflight.check import CheckCallError
from preflight.dnsclient import DnsUnavailable, Referral
from preflight.outcome import Status

SERVERS = ["ns-2.example.", "ns-1.example."]


def delegation(zones=("app",), servers=None):
    return dns.delegated(
        root="tellabs.dev", name_servers={z: list(servers or SERVERS) for z in zones}
    )


def run(bound, tmp_path, fake):
    return observe(bound, make_probe(tmp_path, dns=fake))


def test_delegated_exactly(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))
        }
    )
    assert run(delegation(), tmp_path, fake).status is Status.OK


def test_a_stale_extra_server_fails_with_the_exact_block(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral(
                "referral", frozenset({"ns-1.example", "ns-2.example", "old.example"})
            )
        }
    )
    item = run(delegation(), tmp_path, fake).items[0]
    assert item.key == "app"
    assert item.status is Status.FAIL
    assert (
        item.next_step.paste
        == "app.tellabs.dev. NS ns-1.example.\napp.tellabs.dev. NS ns-2.example."
    )
    assert "replacing any others" in item.next_step.do


def test_no_delegation_and_no_zone_and_unavailable(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral("none", frozenset()),
            "api.tellabs.dev": DnsUnavailable("x"),
        }
    )
    bound = dns.delegated(
        root="tellabs.dev", name_servers={"app": SERVERS, "api": SERVERS, "new": []}
    )
    items = {i.key: i for i in run(bound, tmp_path, fake).items}
    assert items["app"].status is Status.FAIL
    assert items["api"].status is Status.ERROR
    assert items["new"].status is Status.FAIL


def test_no_zones_is_ok(tmp_path):
    assert run(delegation(zones=()), tmp_path, FakeDns()).status is Status.OK


def test_undelegated(tmp_path):
    bound = dns.undelegated(root="tellabs.dev", zones=["old", "gone", "lame"])
    fake = FakeDns(
        referrals={
            "old.tellabs.dev": Referral("referral", frozenset({"ns-1.example"})),
            "gone.tellabs.dev": Referral("none", frozenset()),
            "lame.tellabs.dev": DnsUnavailable("SERVFAIL"),
        }
    )
    items = {i.key: i for i in run(bound, tmp_path, fake).items}
    assert items["old"].status is Status.FAIL
    assert items["gone"].status is Status.OK
    assert items["lame"].status is Status.ERROR  # never ok when nothing answered


def test_cname_with_an_advisory_record(tmp_path):
    bound = dns.cname(
        records=[
            {"name": "vault.tellabs.dev", "target": "vault.app.tellabs.dev"},
            {"name": "wiki.tellabs.dev", "target": "wiki.app.tellabs.dev", "advisory": True},
        ]
    )
    fake = FakeDns(cnames={"vault.tellabs.dev": ["vault.app.tellabs.dev"]})
    result = run(bound, tmp_path, fake)
    items = {i.key: i for i in result.items}
    assert items["vault.tellabs.dev"].status is Status.OK
    assert items["wiki.tellabs.dev"].advisory is True
    assert (
        items["wiki.tellabs.dev"].next_step.paste == "wiki.tellabs.dev. CNAME wiki.app.tellabs.dev."
    )
    assert result.status is Status.OK


def test_delegation_texts_name_no_account(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral("none", frozenset()),
            "old.tellabs.dev": Referral("referral", frozenset({"ns-1.example"})),
        }
    )
    step = run(delegation(), tmp_path, fake).items[0].next_step
    assert step.do == (
        "In the zone that holds tellabs.dev, set the NS record for app.tellabs.dev to exactly "
        "these servers, replacing any others:"
    )
    assert step.paste == "app.tellabs.dev. NS ns-1.example.\napp.tellabs.dev. NS ns-2.example."
    bound = dns.undelegated(root="tellabs.dev", zones=["old"])
    step = run(bound, tmp_path, fake).items[0].next_step
    assert step.do.startswith(
        "In the zone that holds tellabs.dev, remove the NS record for old.tellabs.dev;"
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: dns.caa(domain="tellabs.dev", issuers=[]),
        lambda: dns.caa(domain="tellabs.dev", issuers=['amazon.com"; x']),
        lambda: dns.delegated(root="a.dev", name_servers={"": ["ns.example."]}),
        lambda: dns.delegated(root="a.dev", name_servers={"app": ["ns 1 bad"]}),
        lambda: dns.delegated(root="a.dev", name_servers={"app": [""]}),
        lambda: dns.undelegated(root="a.dev", zones=["old", "old"]),
        lambda: dns.cname(
            records=[
                {"name": "Vault.tellabs.dev", "target": "a.tellabs.dev"},
                {"name": "vault.tellabs.dev.", "target": "b.tellabs.dev"},
            ]
        ),
    ],
)
def test_arguments_are_validated(build):
    with pytest.raises(CheckCallError):
        build()


def test_duplicates_are_named():
    with pytest.raises(CheckCallError, match="duplicate entries: vault.tellabs.dev"):
        dns.cname(
            records=[
                {"name": "Vault.tellabs.dev", "target": "a.tellabs.dev"},
                {"name": "vault.tellabs.dev.", "target": "b.tellabs.dev"},
            ]
        )
    with pytest.raises(CheckCallError, match="duplicate entries: app"):
        dns.undelegated(root="a.dev", zones=["app", "api", "app"])


def test_caa(tmp_path):
    bound = dns.caa(domain="tellabs.dev", issuers=["amazon.com", "amazontrust.com"])
    fake = FakeDns(caa={"tellabs.dev": ['0 issue "amazon.com"', '0 iodef "mailto:x@y"']})
    item = run(bound, tmp_path, fake).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == 'tellabs.dev. CAA 0 issue "amazontrust.com"'
