import importlib.metadata

import preflight


def test_version_matches_the_distribution():
    assert preflight.__version__ == importlib.metadata.version("preflight")
