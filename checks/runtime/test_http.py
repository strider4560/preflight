"""Read-only HTTP probes from existing, explicitly selected execution locations."""

import pytest
import testinfra

from gate.assertions import require

pytestmark = pytest.mark.owner("application-platform")


def test_bootstrap_endpoint(runtime_probe):
    probe = runtime_probe
    host = testinfra.get_host(probe.host)
    # -q must be first to ignore curlrc settings such as 'insecure'.
    # Testinfra quotes %s arguments. The URL is not interpolated into shell source.
    response = host.run(
        "curl -q --fail --silent --show-error --connect-timeout 3 --max-time %s -- %s",
        str(probe.timeout_seconds),
        probe.url,
    )
    require(
        response.rc == 0,
        expected=f"{probe.url} reachable from {probe.host}",
        observed=f"exit {response.rc}: {response.stderr}",
        remediation="Check this probe's DNS, route, security groups, TLS trust and service health.",
    )
    require(
        response.stdout.strip() == probe.expected_body,
        expected=probe.expected_body,
        observed=response.stdout.strip(),
        remediation="Inspect the bootstrap service readiness response.",
    )
