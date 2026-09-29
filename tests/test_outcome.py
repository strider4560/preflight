# tests/test_outcome.py
import json

import pytest

from preflight.outcome import (
    Item,
    Outcome,
    Status,
    apply_remedy,
    error,
    fail,
    ok,
    outcome,
    pending,
)


def test_status_is_the_worst_blocking_item():
    result = outcome(ok("a"), pending("b", wait="an hour"), fail("c", do="Fix c."))
    assert result.status is Status.FAIL


def test_error_outranks_fail_and_pending_outranks_ok():
    assert outcome(fail("a", do="x"), error("b", do="y")).status is Status.ERROR
    assert outcome(ok("a"), pending("b", wait="soon")).status is Status.PENDING


def test_advisory_items_never_block():
    assert outcome(ok("a"), fail("b", do="x", advisory=True)).status is Status.OK


def test_a_single_item_may_be_unnamed():
    assert outcome(ok()).items[0].key is None


@pytest.mark.parametrize("items", [(), (ok("a"), ok("a")), (ok(), ok("b"))])
def test_rejects_empty_duplicate_or_mixed_unnamed_items(items):
    with pytest.raises(ValueError):
        Outcome(items)


def test_round_trips_through_json():
    original = outcome(
        fail("app", do="Set NS.", paste="a NS b", wait="15 min", ref="README", observed=["x"]),
        error("b", do="Sign in.", error_type="Timeout", generic=True),
    )
    assert Outcome.from_dict(json.loads(json.dumps(original.to_dict()))) == original


def test_remedy_fills_empty_fields_and_replaces_only_a_generic_do():
    result = outcome(
        fail("a", do="The stack has changes.", generic=True),
        fail("b", do="Specific.", ref="own"),
    )
    merged = apply_remedy(
        result, {"do": "Rerun bootstrap.sh {environment}.", "ref": "README"}, {"environment": "dev"}
    )
    first, second = merged.items
    assert first.next_step.do == "Rerun bootstrap.sh dev."
    assert first.next_step.ref == "README"
    assert second.next_step.do == "Specific."
    assert second.next_step.ref == "own"


def test_remedy_leaves_ok_items_alone():
    merged = apply_remedy(outcome(ok("a")), {"do": "x"}, {})
    assert merged.items[0].next_step is None


def test_a_blocked_item_never_reads_as_ok():
    assert Outcome((Item("a", Status.BLOCKED),)).status is Status.BLOCKED
    assert outcome(ok("a"), Item("b", Status.BLOCKED, advisory=True)).status is Status.OK
