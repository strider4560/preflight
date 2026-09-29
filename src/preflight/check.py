"""Checks, their contract sections, and instances bound to a section or an identity."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from preflight.identity import Region
from preflight.outcome import Outcome

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
IdentityRef = Name
Binds = Literal["section", "identity"]


class Remedy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    do: str | None = None
    paste: str | None = None
    wait: str | None = None
    ref: str | None = None


class Section(BaseModel):
    """Base of every check's contract section. One model ignores keys it does not declare; the
    graph refuses a key that no model bound to the section declares."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    region: Region | None = None
    timeout: float | None = Field(default=None, gt=0)
    remedy: Remedy = Remedy()


class IdentitySection(Section):
    identity: IdentityRef


@dataclass(frozen=True)
class Requirement:
    """An intrinsic prerequisite: the `check_id` instance for the alias in section `field`."""

    check_id: str
    field: str


def session_for(field: str) -> Requirement:
    return Requirement("aws.session", field)


@dataclass(frozen=True)
class Check:
    id: str
    section: type[Section]
    observe: Callable[[Any, Any], Outcome]
    binds: Binds = "section"
    requires: tuple[Requirement, ...] = ()
    timeout: float = 60.0
    # Run with the caller's AWS variables intact (only aws.assumed).
    ambient: bool = False
    module: str = ""

    def __call__(self, key: str) -> CheckInstance:
        return CheckInstance(self, key)


@dataclass(frozen=True)
class CheckInstance:
    check: Check
    key: str

    @property
    def id(self) -> str:
        return f"{self.check.id}[{self.key}]"


REGISTRY: dict[str, Check] = {}


def check(
    check_id: str,
    *,
    section: type[Section] | None = None,
    binds: Binds = "section",
    requires: tuple[Requirement, ...] | list[Requirement] = (),
    timeout: float = 60.0,
    ambient: bool = False,
) -> Callable[[Callable[[Any, Any], Outcome]], Check]:
    if binds == "identity":
        model = section or IdentitySection
        if not issubclass(model, IdentitySection):
            raise ValueError(f"{check_id}: an identity-bound check's model extends IdentitySection")
    elif section is None:
        raise ValueError(f"{check_id}: a section-bound check needs a section model")
    else:
        model = section

    def decorate(observe: Callable[[Any, Any], Outcome]) -> Check:
        new = Check(
            check_id, model, observe, binds, tuple(requires), timeout, ambient, observe.__module__
        )
        existing = REGISTRY.get(check_id)
        if existing is not None and (existing.module, existing.observe.__qualname__) != (
            new.module,
            observe.__qualname__,
        ):
            raise ValueError(f"check id {check_id!r} is already defined in {existing.module}")
        REGISTRY[check_id] = new
        return new

    return decorate


def _where(prefix: str, loc: tuple[Any, ...]) -> str:
    return ".".join([prefix, *map(str, loc)]) if prefix else ".".join(map(str, loc))


def field_problems(model: type[BaseModel], data: Mapping[str, Any], skip: set[str]) -> list[str]:
    """Validate `data` against `model`, leaving out the fields in `skip` (placeholders and values
    not known until run time). Whole-model validators run only when nothing is skipped."""
    if not skip:
        try:
            model.model_validate(dict(data))
        except ValidationError as exc:
            return [f"{_where('', e['loc'])}: {e['msg']}" for e in exc.errors()]
        return []
    problems = []
    for name, info in model.model_fields.items():
        if name in skip:
            continue
        if name not in data:
            if info.is_required():
                problems.append(f"{name}: Field required")
            continue
        annotation = (
            Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        )
        try:
            TypeAdapter(annotation).validate_python(data[name])
        except ValidationError as exc:
            problems.extend(f"{_where(name, e['loc'])}: {e['msg']}" for e in exc.errors())
    return problems
