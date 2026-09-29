import pytest

from preflight.check import (
    REGISTRY,
    IdentitySection,
    Section,
    check,
    field_problems,
    session_for,
)
from preflight.outcome import ok, outcome


class Things(Section):
    zones: list[str]
    root: str


@check("test.decorated", section=Things, requires=[session_for("identity")], timeout=5)
def decorated(ctx, s):
    return outcome(ok())


def test_the_decorator_registers_a_check_and_binds_instances():
    assert REGISTRY["test.decorated"] is decorated
    assert decorated("delegation").id == "test.decorated[delegation]"
    assert decorated.module == __name__
    assert decorated.timeout == 5
    assert decorated.requires[0].check_id == "aws.session"


def test_identity_bound_checks_use_the_identity_section():
    @check("test.identity_bound", binds="identity")
    def bound(ctx, s):
        return outcome(ok())

    assert bound.section is IdentitySection
    assert bound("admin").id == "test.identity_bound[admin]"


def test_redefining_an_id_in_another_function_is_refused():
    with pytest.raises(ValueError, match="already defined"):

        @check("test.decorated", section=Things)
        def other(ctx, s):
            return outcome(ok())


def test_a_section_bound_check_needs_a_model():
    with pytest.raises(ValueError, match="section model"):

        @check("test.no_model")
        def no_model(ctx, s):
            return outcome(ok())


def test_field_problems_validates_the_whole_model_when_nothing_is_skipped():
    assert field_problems(Things, {"zones": [1], "root": "x"}, set()) == [
        "zones.0: Input should be a valid string"
    ]
    assert field_problems(Things, {"zones": ["app"]}, set()) == ["root: Field required"]


def test_field_problems_leaves_skipped_fields_alone():
    assert field_problems(Things, {"zones": "lazy"}, {"zones"}) == ["root: Field required"]
    assert field_problems(Things, {"zones": "lazy", "root": "x"}, {"zones"}) == []


def test_a_bad_section_region_is_a_field_problem():
    from preflight.check import Section

    problems = field_problems(Section, {"region": "not a region"}, set())
    assert any(p.startswith("region") for p in problems)


def test_unknown_keys_are_ignored_by_one_model():
    assert field_problems(Things, {"zones": [], "root": "x", "other": 1}, set()) == []
