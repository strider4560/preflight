"""Checks: functions whose calls are validated when made and observed later, in a worker."""

from __future__ import annotations

import inspect
import sys
import types
import typing
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypeVar, get_type_hints

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, create_model

from preflight.identity import Identity
from preflight.outcome import Outcome

T = TypeVar("T")


def unique_by(key: Callable[[Any], Hashable]) -> Callable[[list[T]], list[T]]:
    """A list validator refusing entries whose `key` repeats; the error names the repeated keys."""

    def validate(values: list[T]) -> list[T]:
        seen: set[Hashable] = set()
        repeated: list[Hashable] = []
        for value in values:
            marker = key(value)
            if marker in seen and marker not in repeated:
                repeated.append(marker)
            seen.add(marker)
        if repeated:
            raise ValueError(f"duplicate entries: {', '.join(map(str, repeated))}")
        return values

    return validate


unique = unique_by(lambda value: value)
# A list whose entries must differ: `UniqueList[str]`.
UniqueList = Annotated[list[T], AfterValidator(unique)]

ARGUMENTS_CONFIG = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CheckCallError(Exception):
    """A check was called with arguments that do not fit its signature; nothing was observed."""

    def __init__(self, check_id: str, problems: list[str]):
        self.check_id = check_id
        self.problems = list(problems)
        super().__init__(f"{check_id}: " + "; ".join(self.problems))


def _is_identity(annotation: Any) -> bool:
    if annotation is Identity:
        return True
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return Identity in typing.get_args(annotation)
    return False


def _module_label(module: str) -> str:
    if module == "__main__":
        file = getattr(sys.modules.get("__main__"), "__file__", None)
        return Path(file).stem if file else "__main__"
    return module.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Check:
    id: str
    observe: Callable[..., Outcome]
    arguments: type[BaseModel]
    key: str | None = None
    timeout: float = 60.0
    # Run with the caller's AWS variables intact (only aws.assumed).
    ambient: bool = False

    @property
    def module(self) -> str:
        return self.observe.__module__

    @property
    def name(self) -> str:
        return self.observe.__name__

    @property
    def file(self) -> str | None:
        return inspect.getsourcefile(self.observe)

    def __call__(self, *args: Any, timeout: float | None = None, **kwargs: Any) -> BoundCheck:
        try:
            bound = inspect.signature(self.observe).bind(None, *args, **kwargs)
        except TypeError as exc:
            raise CheckCallError(self.id, [str(exc)]) from None
        values = dict(bound.arguments)
        values.pop(next(iter(values)))  # the probe, supplied by the worker
        try:
            arguments = self.arguments.model_validate(values)
        except ValidationError as exc:
            raise CheckCallError(
                self.id,
                [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()],
            ) from None
        if timeout is not None and not timeout > 0:
            raise CheckCallError(self.id, ["timeout: must be greater than 0"])
        return BoundCheck(self, arguments, float(timeout) if timeout else self.timeout)


@dataclass(frozen=True)
class BoundCheck:
    """A check with validated arguments, ready to run in a worker."""

    check: Check
    arguments: BaseModel
    timeout: float

    @property
    def values(self) -> dict[str, Any]:
        return {name: getattr(self.arguments, name) for name in type(self.arguments).model_fields}

    @property
    def label(self) -> str:
        if self.check.key is None:
            return self.check.id
        return f"{self.check.id}({getattr(self.arguments, self.check.key)})"

    @property
    def identity(self) -> Identity | None:
        return next((v for v in self.values.values() if isinstance(v, Identity)), None)

    def arguments_json(self) -> str:
        return self.arguments.model_dump_json()


def _arguments_model(fn: Callable[..., Outcome]) -> type[BaseModel]:
    parameters = list(inspect.signature(fn).parameters.values())
    if not parameters:
        raise TypeError(f"{fn.__qualname__}: a check's first parameter is the probe")
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, Any] = {}
    for parameter in parameters[1:]:
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            star = "*" if parameter.kind is parameter.VAR_POSITIONAL else "**"
            raise TypeError(f"{fn.__qualname__}: {star}{parameter.name} is not supported")
        if parameter.name == "timeout":
            raise TypeError(f"{fn.__qualname__}: timeout is reserved for every check call")
        if parameter.name not in hints:
            raise TypeError(
                f"{fn.__qualname__}: parameter {parameter.name} needs a type annotation"
            )
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (hints[parameter.name], default)
    return create_model(f"{fn.__name__}_arguments", __config__=ARGUMENTS_CONFIG, **fields)


def check(
    fn: Callable[..., Outcome] | None = None,
    /,
    *,
    key: str | None = None,
    timeout: float = 60.0,
    ambient: bool = False,
) -> Any:
    """`@check` or `@check(key=..., timeout=..., ambient=...)` on a module-level function whose
    first parameter is the probe and whose other parameters are annotated."""

    def decorate(fn: Callable[..., Outcome]) -> Check:
        model = _arguments_model(fn)
        if key is not None and key not in model.model_fields:
            raise TypeError(f"{fn.__qualname__}: key {key} is not a parameter")
        identities = [n for n, f in model.model_fields.items() if _is_identity(f.annotation)]
        if len(identities) > 1:
            raise TypeError(f"{fn.__qualname__}: a check takes at most one aws.Identity")
        if fn.__qualname__ != fn.__name__:
            raise TypeError(f"{fn.__qualname__}: define checks at module level for workers")
        return Check(
            f"{_module_label(fn.__module__)}.{fn.__name__}", fn, model, key, timeout, ambient
        )

    return decorate(fn) if fn is not None else decorate
