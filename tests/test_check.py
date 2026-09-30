import pytest
from fakes import IDENTITY

from preflight.check import BoundCheck, CheckCallError, check
from preflight.identity import Identity
from preflight.outcome import Outcome, ok, outcome
from preflight.probe import Probe


@check(key="name")
def sample(probe: Probe, name: str, count: int = 1, tags: tuple[str, ...] = ()) -> Outcome:
    return outcome(ok(observed=[name, count, list(tags)]))


@check(ambient=True, timeout=5)
def with_identity(probe: Probe, identity: Identity) -> Outcome:
    return outcome(ok())


def test_calling_a_check_validates_and_binds_without_observing():
    bound = sample("a", count="2")
    assert isinstance(bound, BoundCheck)
    assert bound.values == {"name": "a", "count": 2, "tags": ()}
    assert (bound.label, bound.timeout) == ("test_check.sample(a)", 60.0)
    assert bound.identity is None


def test_ids_come_from_the_module_and_function():
    assert sample.id == "test_check.sample"
    assert (sample.module, sample.name) == ("test_check", "sample")
    assert sample.file is not None and sample.file.endswith("test_check.py")
    assert with_identity(identity=IDENTITY).label == "test_check.with_identity"


def test_wrong_types_raise_a_check_call_error_naming_the_field():
    with pytest.raises(CheckCallError, match=r"^test_check\.sample: count: Input should be"):
        sample("a", count="many")


def test_missing_and_unknown_arguments_are_refused():
    with pytest.raises(CheckCallError, match="missing a required argument: 'name'"):
        sample()
    with pytest.raises(CheckCallError, match="unexpected keyword argument 'colour'"):
        sample("a", colour="red")


def test_timeout_is_a_reserved_keyword_of_every_call():
    assert sample("a", timeout=5).timeout == 5
    assert with_identity(identity=IDENTITY).timeout == 5
    with pytest.raises(CheckCallError, match="timeout: must be greater than 0"):
        sample("a", timeout=0)


def test_the_identity_argument_is_found_and_survives_json():
    bound = with_identity(identity=IDENTITY)
    assert bound.identity == IDENTITY
    again = with_identity.arguments.model_validate_json(bound.arguments_json())
    assert again.identity == IDENTITY


def test_checks_are_hashable_so_tests_can_key_stand_ins_by_check():
    assert {sample: 1}[sample] == 1


def test_definition_errors():
    def nothing() -> Outcome: ...

    def unannotated(probe, name) -> Outcome: ...

    def reserved(probe, timeout: int) -> Outcome: ...

    def two(probe, a: Identity, b: Identity | None = None) -> Outcome: ...

    def star(probe, *names: str) -> Outcome: ...

    def fine(probe, name: str) -> Outcome: ...

    with pytest.raises(TypeError, match="first parameter is the probe"):
        check(nothing)
    with pytest.raises(TypeError, match="parameter name needs a type annotation"):
        check(unannotated)
    with pytest.raises(TypeError, match="timeout is reserved"):
        check(reserved)
    with pytest.raises(TypeError, match="at most one aws.Identity"):
        check(two)
    with pytest.raises(TypeError, match=r"\*names is not supported"):
        check(star)
    with pytest.raises(TypeError, match="key other is not a parameter"):
        check(key="other")(fine)
    with pytest.raises(TypeError, match="define checks at module level"):
        check(fine)
