"""A certificate the operator validates by hand: issued, or exactly which record is missing.
Pending counts as the operator's step until the validation CNAME is visible."""

from __future__ import annotations

from typing import Any

from preflight.check import IdentityRef, Section, check, session_for
from preflight.dnsclient import DnsUnavailable, norm
from preflight.outcome import Outcome, error, fail, ok, outcome, pending


class CertificateSection(Section):
    identity: IdentityRef
    certificate_arn: str


def _validation_records(certificate: dict[str, Any]) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for option in certificate.get("domain_validation_options") or []:
        record = option.get("resource_record")
        if record and record.get("type", "CNAME") == "CNAME":
            records[norm(record["name"])] = {"name": record["name"], "value": record["value"]}
    return list(records.values())


@check("acm.issued", section=CertificateSection, requires=[session_for("identity")])
def issued(ctx, s: CertificateSection) -> Outcome:
    try:
        info = ctx.aws_module(
            "community.aws.acm_certificate_info",
            {"certificate_arn": s.certificate_arn},
            expect=("certificates",),
        )
    except Exception as exc:
        return outcome(
            error(
                do=(
                    "Could not describe the certificate; check that profile "
                    f"{ctx.identity.profile} may call acm:DescribeCertificate."
                ),
                error_type=type(exc).__name__,
            )
        )
    certificates = info.get("certificates") or []
    if not certificates:
        return outcome(
            fail(
                do=f"No certificate {s.certificate_arn} exists in {ctx.identity.region}.",
                generic=True,
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
                generic=True,
            )
        )
    records = _validation_records(certificates[0])
    if not records:
        return outcome(
            pending(wait="ACM has not published the validation record yet; recheck in a minute.")
        )
    try:
        missing = [r for r in records if norm(r["value"]) not in ctx.dns.cname(r["name"])]
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
                do="In Administration, add the certificate's validation record(s):",
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
