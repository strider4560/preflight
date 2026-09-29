"""DNS observations: the parent-side delegation of a name, and recursive CNAME and CAA lookups.

A delegation is read from the parent's own servers with a non-recursive query, because a
recursive resolver answers with the child's NS as soon as any delegated server works."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset


class DnsUnavailable(Exception):
    """No server gave a usable answer; never a basis for an ok."""


def norm(name: str) -> str:
    return name.strip().lower().rstrip(".")


class Transport(Protocol):
    def query(
        self, message: dns.message.Message, server: str, timeout: float
    ) -> dns.message.Message: ...

    def resolve(self, name: str, rdtype: str) -> list[str]: ...


class SystemTransport:
    def query(
        self, message: dns.message.Message, server: str, timeout: float
    ) -> dns.message.Message:
        response = dns.query.udp(message, server, timeout=timeout)
        if response.flags & dns.flags.TC:
            response = dns.query.tcp(message, server, timeout=timeout)
        return response

    def resolve(self, name: str, rdtype: str) -> list[str]:
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = 5.0
            answer = resolver.resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as exc:
            raise DnsUnavailable(f"{name} {rdtype}: {type(exc).__name__}") from None
        return [record.to_text() for record in answer]


@dataclass(frozen=True)
class Referral:
    kind: Literal["referral", "none"]
    servers: frozenset[str]


class DnsClient:
    def __init__(self, transport: Transport | None = None, timeout: float = 3.0):
        self.transport = transport or SystemTransport()
        self.timeout = timeout

    def parent_addresses(self, parent: str) -> list[str]:
        servers = sorted(norm(s) for s in self.transport.resolve(parent, "NS"))
        if not servers:
            raise DnsUnavailable(f"{parent} has no NS records")
        addresses = [a for server in servers for a in self.transport.resolve(server, "A")]
        if not addresses:
            raise DnsUnavailable(f"the name servers of {parent} have no addresses")
        return addresses

    def referral(self, name: str, parent: str) -> Referral:
        query = dns.message.make_query(name, dns.rdatatype.NS)
        query.flags &= ~dns.flags.RD
        target = dns.name.from_text(name)
        usable: set[Referral] = set()
        for address in self.parent_addresses(parent):
            try:
                response = self.transport.query(query, address, self.timeout)
            except (dns.exception.DNSException, OSError):
                continue
            answer = _usable(response, target)
            if answer is not None:
                usable.add(answer)
        if not usable:
            raise DnsUnavailable(f"no server for {parent} answered about {name}")
        if len(usable) > 1:
            raise DnsUnavailable(f"parent servers disagree about {name}")
        return usable.pop()

    def cname(self, name: str) -> list[str]:
        return [norm(target) for target in self.transport.resolve(name, "CNAME")]

    def caa(self, name: str) -> list[str]:
        return self.transport.resolve(name, "CAA")


def _has_ns(section: list, target: dns.name.Name) -> dns.rrset.RRset | None:
    for rrset in section:
        if rrset.rdtype == dns.rdatatype.NS and rrset.name == target:
            return rrset
    return None


def _usable(response: dns.message.Message, target: dns.name.Name) -> Referral | None:
    """The parent's verdict, or None when the response cannot support one."""
    code = response.rcode()
    authoritative = bool(response.flags & dns.flags.AA)
    if _has_ns(response.answer, target) is not None:
        return None
    if code == dns.rcode.NXDOMAIN:
        return Referral("none", frozenset()) if authoritative else None
    if code != dns.rcode.NOERROR:
        return None
    delegation = _has_ns(response.authority, target)
    if delegation is not None:
        return Referral("referral", frozenset(norm(r.target.to_text()) for r in delegation))
    has_soa = any(r.rdtype == dns.rdatatype.SOA for r in response.authority)
    if authoritative and has_soa:
        return Referral("none", frozenset())
    return None
