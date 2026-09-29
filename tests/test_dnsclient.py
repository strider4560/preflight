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
        if code == dns.rcode.NXDOMAIN:
            response.flags |= dns.flags.AA
        return response

    return answer


def nodata(query):
    response = dns.message.make_response(query)
    response.flags |= dns.flags.AA
    response.authority.append(
        dns.rrset.from_text("tellabs.dev.", 300, "IN", "SOA", "ns-a.example. h.example. 1 2 3 4 5")
    )
    return response


def lame(query):
    return dns.message.make_response(query)


def child_answer(query):
    response = dns.message.make_response(query)
    response.flags |= dns.flags.AA
    response.answer.append(
        dns.rrset.from_text("app.tellabs.dev.", 300, "IN", "NS", "ns-1.example.")
    )
    return response


def test_norm():
    assert norm(" NS-1.Example.COM. ") == "ns-1.example.com"


def test_reads_the_referral_from_the_authority_section_without_recursion():
    both = referral("NS-1.example.", "ns-2.example.")
    transport = FakeTransport(responses={"192.0.2.1": both, "192.0.2.2": both})
    result = DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")
    assert result == Referral("referral", frozenset({"ns-1.example", "ns-2.example"}))
    server, message = transport.queries[0]
    assert server == "192.0.2.1"
    assert not message.flags & dns.flags.RD


def test_nxdomain_and_an_empty_authority_mean_no_delegation():
    for answer in (rcode(dns.rcode.NXDOMAIN), nodata):
        transport = FakeTransport(responses={"192.0.2.1": answer, "192.0.2.2": answer})
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


def test_a_plain_empty_noerror_is_not_a_verdict():
    transport = FakeTransport(responses={"192.0.2.1": rcode(dns.rcode.NOERROR), "192.0.2.2": lame})
    with pytest.raises(DnsUnavailable):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_a_lame_server_is_skipped_for_a_real_referral():
    transport = FakeTransport(responses={"192.0.2.1": lame, "192.0.2.2": referral("ns-1.example.")})
    result = DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")
    assert result == Referral("referral", frozenset({"ns-1.example"}))


def test_all_servers_lame_is_unavailable():
    transport = FakeTransport(responses={"192.0.2.1": lame, "192.0.2.2": lame})
    with pytest.raises(DnsUnavailable):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_an_ns_rrset_only_in_the_answer_section_is_unusable():
    transport = FakeTransport(responses={"192.0.2.1": child_answer, "192.0.2.2": child_answer})
    with pytest.raises(DnsUnavailable):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")


def test_disagreeing_parent_servers_are_unavailable():
    transport = FakeTransport(
        responses={"192.0.2.1": referral("ns-1.example."), "192.0.2.2": rcode(dns.rcode.NXDOMAIN)}
    )
    with pytest.raises(DnsUnavailable, match="disagree"):
        DnsClient(transport).referral("app.tellabs.dev", "tellabs.dev")
