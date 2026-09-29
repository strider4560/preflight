import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest

from preflight.dnsclient import DnsClient, DnsUnavailable, Referral, norm

PARENT = {
    ("tellabs.dev", "NS"): ["ns-a.example.", "ns-b.example."],
    ("ns-a.example", "A"): ["192.0.2.1"],
    ("ns-b.example", "A"): ["192.0.2.2"],
}


class FakeTransport:
    def __init__(self, answers=None, responses=None):
        self.answers = {**PARENT, **(answers or {})}
        self.responses = responses or {}
        self.queries = []

    def resolve(self, name, rdtype):
        value = self.answers.get((name, rdtype), [])
        if isinstance(value, Exception):
            raise value
        return value

    def query(self, message, server, timeout):
        self.queries.append((server, message))
        value = self.responses[server]
        if isinstance(value, Exception):
            raise value
        return value(message)


def referral(*servers):
    def answer(query):
        response = dns.message.make_response(query)
        response.authority.append(
            dns.rrset.from_text("app.tellabs.dev.", 172800, "IN", "NS", *servers)
        )
        return response

    return answer


def rcode(code):
    def answer(query):
        response = dns.message.make_response(query)
        response.set_rcode(code)
        return response

    return answer


def test_norm():
    assert norm(" NS-1.Example.COM. ") == "ns-1.example.com"


def test_reads_the_referral_from_the_authority_section_without_recursion():
    transport = FakeTransport(responses={"192.0.2.1": referral("NS-1.example.", "ns-2.example.")})
    result = DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")
    assert result == Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))
    server, message = transport.queries[0]
    assert server == "192.0.2.1"
    assert not message.flags & dns.flags.RD


def test_nxdomain_and_an_empty_authority_mean_no_delegation():
    for answer in (rcode(dns.rcode.NXDOMAIN), rcode(dns.rcode.NOERROR)):
        transport = FakeTransport(responses={"192.0.2.1": answer})
        assert DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev").kind == "none"


def test_a_server_that_times_out_is_skipped():
    transport = FakeTransport(
        responses={"192.0.2.1": dns.exception.Timeout(), "192.0.2.2": referral("ns-1.example.")}
    )
    assert DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev").kind == "referral"


def test_no_usable_answer_is_unavailable_never_none():
    transport = FakeTransport(
        responses={"192.0.2.1": rcode(dns.rcode.SERVFAIL), "192.0.2.2": rcode(dns.rcode.REFUSED)}
    )
    with pytest.raises(DnsUnavailable):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_a_parent_without_name_servers_is_unavailable():
    transport = FakeTransport(answers={("tellabs.dev", "NS"): []})
    with pytest.raises(DnsUnavailable, match="no NS records"):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_cname_and_caa():
    transport = FakeTransport(
        answers={
            ("vault.tellabs.dev", "CNAME"): ["Vault.App.Tellabs.Dev."],
            ("tellabs.dev", "CAA"): ['0 issue "amazon.com"'],
        }
    )
    client = DnsClient(transport)
    assert client.cname("vault.tellabs.dev") == ["vault.app.tellabs.dev"]
    assert client.caa("tellabs.dev") == ['0 issue "amazon.com"']
    assert client.cname("missing.tellabs.dev") == []
