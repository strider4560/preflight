"""Identity verification is also performed implicitly before every aws.client call."""

import pytest

pytestmark = pytest.mark.owner("cloud-platform")


def test_effective_identity(aws, identity_name):
    # Raises with expected/observed identity details if the credentials are wrong.
    aws.identity(identity_name)
