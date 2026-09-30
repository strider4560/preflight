"""A certificate the operator validates by hand: issued, or exactly which record is missing.
Pending counts as the operator's step until the validation CNAME is visible."""

from __future__ import annotations

from typing import Any

from preflight.check import check
from preflight.dnsclient import DnsUnavailable, norm
from preflight.identity import Identity
from preflight.outcome import Outcome, error, fail, ok, outcome, pending
from preflight.probe import Probe


def _validation_records(certificate: dict[str, Any]) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for option in certificate.get("domain_validation_options") or []:
        record = option.get("resource_record")
        if record and record.get("type", "CNAME") == "CNAME":
            records[norm(record["name"])] = {"name": record["name"], "value": record["value"]}
    return list(records.values())


@check(key="arn")
def issued(probe: Probe, arn: str, identity: Identity) -> Outcome:
    try:
        info = probe.aws_module(
            "community.aws.acm_certificate_info",
            {"certificate_arn": arn},
            expect=("certificates",),
        )
    except Exception as exc:
        return outcome(
            error(
                do=(
                    "Could not describe the certificate; check that profile "
                    f"{identity.profile} may call acm:DescribeCertificate."
                ),
                error_type=type(exc).__name__,
            )
        )
    certificates = info.get("certificates") or []
    if not certificates:
        return outcome(
            fail(
                do=f"No certificate {arn} exists in {identity.region}.",
            )
        )
    status = certificates[0].get("status", "UNKNOWN")
    if status == "ISSUED":
        return outcome(ok(observed=status))
    if status != "PENDING_VALIDATION":
        return outcome(
            fail(
                do=f"The certificate is {status}; request a new one.",
                observed=status,
            )
        )
    records = _validation_records(certificates[0])
    if not records:
        return outcome(
            pending(wait="ACM has not published the validation record yet; recheck in a minute.")
        )
    try:
        missing = [r for r in records if norm(r["value"]) not in probe.dns.cname(r["name"])]
    except DnsUnavailable:
        return outcome(
            error(
                do="Could not look up the certificate's validation records; check DNS, then rerun.",
                error_type="DnsUnavailable",
            )
        )
    if missing:
        return outcome(
            fail(
                do="Add the certificate's validation record(s) in the zone that holds them:",
                paste="\n".join(f"{norm(r['name'])}. CNAME {norm(r['value'])}." for r in missing),
                observed=status,
            )
        )
    return outcome(
        pending(
            wait=(
                "ACM usually issues within minutes of seeing the record, and gives up 72 hours "
                "after the request; recheck later."
            ),
            observed=status,
        )
    )
