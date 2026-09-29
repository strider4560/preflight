"""DNS the operator manages by hand: delegations held by the parent zone, CNAMEs, and CAA.
A lookup nobody answered is an error, never an ok."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from preflight.check import Section, check, unique_by
from preflight.dnsclient import DnsUnavailable, norm
from preflight.outcome import Item, Outcome, error, fail, ok, outcome

Domain = Annotated[
    str,
    StringConstraints(pattern=r"^([A-Za-z0-9_]([A-Za-z0-9_-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\.?$"),
]
Label = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")]
NEGATIVE_CACHE = (
    "resolvers may keep the old answer for up to the zone's negative-cache TTL "
    "(its SOA minimum) after a fix"
)


# Names compare as DNS does: without case or a trailing dot.
Zones = Annotated[list[Label], AfterValidator(unique_by(norm))]


class DelegationSection(Section):
    root: Domain
    zones: Zones
    name_servers: dict[Label, list[Domain]]


class UndelegatedSection(Section):
    root: Domain
    zones: Zones = []


class CnameRecord(BaseModel):
    name: Domain
    target: Domain
    advisory: bool = False


class CnameSection(Section):
    records: Annotated[
        list[CnameRecord], AfterValidator(unique_by(lambda record: norm(record.name)))
    ]


class CaaSection(Section):
    domain: Domain
    issuers: list[Domain] = Field(min_length=1)


def ns_block(name: str, servers) -> str:
    return "\n".join(f"{name}. NS {server}." for server in sorted(servers))


def _all(items: list[Item]) -> Outcome:
    return outcome(*items) if items else outcome(ok(observed="nothing to check"))


def _unavailable(key: str | None, parent: str, advisory: bool = False) -> Item:
    return error(
        key,
        do=(
            f"No name server for {parent} gave a usable answer; "
            "check your network and DNS, then rerun."
        ),
        error_type="DnsUnavailable",
        advisory=advisory,
    )


@check("dns.delegated", section=DelegationSection)
def delegated(ctx, s: DelegationSection) -> Outcome:
    root = norm(s.root)
    items = []
    for prefix in s.zones:
        name = f"{prefix}.{root}"
        expected = {norm(server) for server in s.name_servers.get(prefix, [])}
        if not expected:
            items.append(
                fail(
                    prefix,
                    do=f"{name} has no zone yet (no name servers recorded for it).",
                    generic=True,
                )
            )
            continue
        try:
            referral = ctx.dns.referral(name, root)
        except DnsUnavailable:
            items.append(_unavailable(prefix, root))
            continue
        if referral.kind == "referral" and set(referral.servers) == expected:
            items.append(ok(prefix, observed=sorted(referral.servers)))
            continue
        items.append(
            fail(
                prefix,
                do=(
                    f"In the zone that holds {root}, set the NS record for {name} to exactly "
                    "these servers, replacing any others:"
                ),
                paste=ns_block(name, expected),
                observed=sorted(referral.servers),
                generic=True,
            )
        )
    return _all(items)


@check("dns.undelegated", section=UndelegatedSection)
def undelegated(ctx, s: UndelegatedSection) -> Outcome:
    root = norm(s.root)
    items = []
    for prefix in s.zones:
        name = f"{prefix}.{root}"
        try:
            referral = ctx.dns.referral(name, root)
        except DnsUnavailable:
            items.append(_unavailable(prefix, root))
            continue
        if referral.kind == "none":
            items.append(ok(prefix))
        else:
            items.append(
                fail(
                    prefix,
                    do=(
                        f"In the zone that holds {root}, remove the NS record for {name}; a "
                        "delegation to a zone being retired is a takeover risk."
                    ),
                    observed=sorted(referral.servers),
                    generic=True,
                )
            )
    return _all(items)


@check("dns.cname", section=CnameSection)
def cname(ctx, s: CnameSection) -> Outcome:
    items = []
    for record in s.records:
        name, target = norm(record.name), norm(record.target)
        try:
            targets = ctx.dns.cname(name)
        except DnsUnavailable:
            items.append(_unavailable(name, name, record.advisory))
            continue
        if target in targets:
            items.append(ok(name))
        else:
            items.append(
                fail(
                    name,
                    do=f"Add the CNAME {name} → {target}.",
                    paste=f"{name}. CNAME {target}.",
                    wait=NEGATIVE_CACHE,
                    observed=targets,
                    advisory=record.advisory,
                )
            )
    return _all(items)


@check("dns.caa", section=CaaSection)
def caa(ctx, s: CaaSection) -> Outcome:
    domain = norm(s.domain)
    try:
        records = ctx.dns.caa(domain)
    except DnsUnavailable:
        return outcome(_unavailable(None, domain))
    allowed = set()
    for record in records:
        parts = record.split(None, 2)
        if len(parts) == 3 and parts[1].lower() == "issue":
            allowed.add(parts[2].strip('"').split(";")[0].strip().lower())
    missing = [issuer for issuer in s.issuers if issuer.lower() not in allowed]
    if not missing:
        return outcome(ok(observed=sorted(allowed)))
    return outcome(
        fail(
            do=f"Add a CAA record to {domain} allowing {', '.join(missing)}.",
            paste="\n".join(f'{domain}. CAA 0 issue "{issuer}"' for issuer in missing),
            wait=NEGATIVE_CACHE,
            observed=sorted(allowed),
        )
    )
