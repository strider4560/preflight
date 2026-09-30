"""DNS the operator manages by hand: delegations held by the parent zone, CNAMEs, and CAA.
A lookup nobody answered is an error, never an ok."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from preflight.check import check, unique_by
from preflight.dnsclient import DnsUnavailable, norm
from preflight.outcome import Item, Outcome, error, fail, ok, outcome
from preflight.probe import Probe

Domain = Annotated[
    str,
    StringConstraints(pattern=r"^([A-Za-z0-9_]([A-Za-z0-9_-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}\.?$"),
]
Label = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")]
NEGATIVE_CACHE = (
    "resolvers may keep the old answer for up to the zone's negative-cache TTL "
    "(the lower of its SOA record's TTL and SOA minimum) after a fix"
)


# Names compare as DNS does: without case or a trailing dot.
Zones = Annotated[list[Label], AfterValidator(unique_by(norm))]


class CnameRecord(BaseModel):
    name: Domain
    target: Domain
    advisory: bool = False


Records = Annotated[list[CnameRecord], AfterValidator(unique_by(lambda record: norm(record.name)))]


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


@check(key="root")
def delegated(
    probe: Probe,
    root: Domain,
    name_servers: Annotated[dict[Label, list[Domain]], Field(min_length=1)],
) -> Outcome:
    root = norm(root)
    items = []
    for prefix, servers in name_servers.items():
        name = f"{prefix}.{root}"
        expected = {norm(server) for server in servers}
        if not expected:
            items.append(
                fail(
                    prefix,
                    do=f"{name} has no zone yet (no name servers recorded for it).",
                )
            )
            continue
        try:
            referral = probe.dns.referral(name, root)
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
            )
        )
    return _all(items)


@check(key="root")
def undelegated(
    probe: Probe, root: Domain, zones: Annotated[Zones, Field(min_length=1)]
) -> Outcome:
    root = norm(root)
    items = []
    for prefix in zones:
        name = f"{prefix}.{root}"
        try:
            referral = probe.dns.referral(name, root)
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
                )
            )
    return _all(items)


@check
def cname(probe: Probe, records: Annotated[Records, Field(min_length=1)]) -> Outcome:
    items = []
    for record in records:
        name, target = norm(record.name), norm(record.target)
        try:
            targets = probe.dns.cname(name)
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


@check(key="domain")
def caa(
    probe: Probe, domain: Domain, issuers: Annotated[list[Domain], Field(min_length=1)]
) -> Outcome:
    domain = norm(domain)
    try:
        records = probe.dns.caa(domain)
    except DnsUnavailable:
        return outcome(_unavailable(None, domain))
    allowed = set()
    for record in records:
        parts = record.split(None, 2)
        if len(parts) == 3 and parts[1].lower() == "issue":
            allowed.add(parts[2].strip('"').split(";")[0].strip().lower())
    missing = [issuer for issuer in issuers if issuer.lower() not in allowed]
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
