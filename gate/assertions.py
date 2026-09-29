"""Optional operator-facing assertions; ordinary pytest assertions also work."""

import pytest


def require(condition, *, expected, observed, remediation):
    if not condition:
        pytest.fail(
            f"Expected: {expected}\nObserved: {observed}\nRemediation: {remediation}",
            pytrace=False,
        )
