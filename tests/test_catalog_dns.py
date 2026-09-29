from fakes import FakeDns, make_ctx

from preflight.catalog import dns
from preflight.dnsclient import DnsUnavailable, Referral
from preflight.outcome import Status

SERVERS = ["ns-2.example.", "ns-1.example."]


def delegation(zones=("app",), servers=None):
    return dns.DelegationSection(
        root="tellabs.dev", zones=list(zones), name_servers={"app": servers or SERVERS}
    )


def run(check, section, tmp_path, fake):
    return check.observe(make_ctx(tmp_path, dns=fake), section)


def test_delegated_exactly(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))
        }
    )
    assert run(dns.delegated, delegation(), tmp_path, fake).status is Status.OK


def test_a_stale_extra_server_fails_with_the_exact_block(tmp_path):
    fake = FakeDns(
        referrals={
            "app.tellabs.dev": Referral(
                "referral", frozenset({"ns-1.example", "ns-2.example", "old.example"})
            )
        }
    )
    item = run(dns.delegated, delegation(), tmp_path, fake).items[0]
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
    section = dns.DelegationSection(
        root="tellabs.dev",
        zones=["app", "api", "new"],
        name_servers={"app": SERVERS, "api": SERVERS},
    )
    items = {i.key: i for i in run(dns.delegated, section, tmp_path, fake).items}
    assert items["app"].status is Status.FAIL
    assert items["api"].status is Status.ERROR
    assert (items["new"].status, items["new"].next_step.generic) == (Status.FAIL, True)


def test_no_zones_is_ok(tmp_path):
    assert run(dns.delegated, delegation(zones=()), tmp_path, FakeDns()).status is Status.OK


def test_undelegated(tmp_path):
    section = dns.UndelegatedSection(root="tellabs.dev", zones=["old", "gone", "lame"])
    fake = FakeDns(
        referrals={
            "old.tellabs.dev": Referral("referral", frozenset({"ns-1.example"})),
            "gone.tellabs.dev": Referral("none", frozenset()),
            "lame.tellabs.dev": DnsUnavailable("SERVFAIL"),
        }
    )
    items = {i.key: i for i in run(dns.undelegated, section, tmp_path, fake).items}
    assert items["old"].status is Status.FAIL
    assert items["gone"].status is Status.OK
    assert items["lame"].status is Status.ERROR  # never ok when nothing answered


def test_cname_with_an_advisory_record(tmp_path):
    section = dns.CnameSection(
        records=[
            {"name": "vault.tellabs.dev", "target": "vault.app.tellabs.dev"},
            {"name": "wiki.tellabs.dev", "target": "wiki.app.tellabs.dev", "advisory": True},
        ]
    )
    fake = FakeDns(cnames={"vault.tellabs.dev": ["vault.app.tellabs.dev"]})
    result = run(dns.cname, section, tmp_path, fake)
    items = {i.key: i for i in result.items}
    assert items["vault.tellabs.dev"].status is Status.OK
    assert items["wiki.tellabs.dev"].advisory is True
    assert (
        items["wiki.tellabs.dev"].next_step.paste == "wiki.tellabs.dev. CNAME wiki.app.tellabs.dev."
    )
    assert result.status is Status.OK


def test_caa(tmp_path):
    section = dns.CaaSection(domain="tellabs.dev", issuers=["amazon.com", "amazontrust.com"])
    fake = FakeDns(caa={"tellabs.dev": ['0 issue "amazon.com"', '0 iodef "mailto:x@y"']})
    item = run(dns.caa, section, tmp_path, fake).items[0]
    assert item.status is Status.FAIL
    assert item.next_step.paste == 'tellabs.dev. CAA 0 issue "amazontrust.com"'
