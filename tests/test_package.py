import importlib.metadata

import preflight


def test_version_matches_the_distribution():
    assert preflight.__version__ == importlib.metadata.version("preflight")


def test_not_observed_is_exported_as_a_string():
    assert preflight.NOT_OBSERVED == "not observed (--validate)"
    assert "NOT_OBSERVED" in preflight.__all__
