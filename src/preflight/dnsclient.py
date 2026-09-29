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
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        try:
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
        for address in self.parent_addresses(parent):
            try:
                response = self.transport.query(query, address, self.timeout)
            except (dns.exception.DNSException, OSError):
                continue
            code = response.rcode()
            if code == dns.rcode.NXDOMAIN:
                return Referral("none", frozenset())
            if code != dns.rcode.NOERROR:
                continue
            for rrset in response.authority:
                if rrset.rdtype == dns.rdatatype.NS and rrset.name == target:
                    return Referral("referral", frozenset(norm(r.target.to_text()) for r in rrset))
            return Referral("none", frozenset())
        raise DnsUnavailable(f"no server for {parent} answered about {name}")

    def cname(self, name: str) -> list[str]:
        return [norm(target) for target in self.transport.resolve(name, "CNAME")]

    def caa(self, name: str) -> list[str]:
        return self.transport.resolve(name, "CAA")
