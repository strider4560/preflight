"""Gate inputs and dependencies, as FastAPI resolves a handler's parameters: `Arg` comes from
the command line, `Depends` from a provider called once per run, when first needed."""

from __future__ import annotations

import argparse
import contextlib
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter, ValidationError

from preflight.outcome import Item, Outcome

EMPTY = inspect.Parameter.empty


@dataclass(frozen=True)
class Arg:
    """A gate input from the command line: positional without a default, `--name` with one."""

    help: str | None = None


@dataclass(frozen=True)
class Depends:
    """A value from `provider`, called once per run with its own parameters resolved."""

    provider: Callable[..., Any]


class GateDefinitionError(Exception):
    """The gate is wrong in a way found before anything is observed (exit 2)."""

    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class Unmet(Exception):
    """A provider cannot provide. Carries the next steps; the guard needing it stops the run."""

    def __init__(self, *reasons: Item | Outcome):
        items: list[Item] = []
        for reason in reasons:
            items.extend(reason.items if isinstance(reason, Outcome) else [reason])
        if not items:
            raise ValueError("Unmet needs at least one item")
        self.items = tuple(items)
        self.provider: str | None = None
        super().__init__(f"{len(self.items)} unmet item(s)")


class Propagate(Exception):
    """Raised by an executor to leave a run as it is, never wrapped as a provider failure."""


class ProviderFailed(Exception):
    """A provider raised something other than Unmet: its name and the exception type only."""

    def __init__(self, provider: str, error_type: str):
        self.provider = provider
        self.error_type = error_type
        super().__init__(f"provider {provider} raised {error_type}")


@dataclass(frozen=True)
class Parameter:
    name: str
    annotation: Any
    default: Any
    marker: Arg | Depends


@dataclass(frozen=True)
class ArgSpec:
    name: str
    annotation: Any
    default: Any
    help: str | None


def _name(fn: Callable[..., Any]) -> str:
    return getattr(fn, "__name__", repr(fn))


def parameters_of(fn: Callable[..., Any]) -> list[Parameter]:
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception as exc:
        raise GateDefinitionError(
            [f"{_name(fn)}: its annotations cannot be read ({type(exc).__name__})"]
        ) from None
    found: list[Parameter] = []
    problems: list[str] = []
    for parameter in inspect.signature(fn).parameters.values():
        hint = hints.get(parameter.name)
        marker, base = None, hint
        if get_origin(hint) is Annotated:
            base, *extras = get_args(hint)
            marker = next((e for e in extras if isinstance(e, (Arg, Depends))), None)
        if marker is None or parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            problems.append(
                f"{_name(fn)}: parameter {parameter.name} needs Annotated[<type>, Arg()] "
                "or Annotated[<type>, Depends(<provider>)]"
            )
            continue
        found.append(Parameter(parameter.name, base, parameter.default, marker))
    if problems:
        raise GateDefinitionError(problems)
    return found


def collect_args(
    roots: Sequence[Callable[..., Any]], overrides: Mapping[Callable[..., Any], Callable[..., Any]]
) -> list[ArgSpec]:
    """Every `Arg` the roots and their providers declare, merged by name, in first-seen order."""
    specs: dict[str, ArgSpec] = {}
    problems: list[str] = []
    seen: set[Callable[..., Any]] = set()

    def visit(fn: Callable[..., Any], trail: tuple[Callable[..., Any], ...]) -> None:
        if fn in trail:
            cycle = " -> ".join(_name(f) for f in (*trail, fn))
            problems.append(f"providers depend on each other in a cycle: {cycle}")
            return
        if fn in seen:
            return
        seen.add(fn)
        for parameter in parameters_of(fn):
            if isinstance(parameter.marker, Depends):
                provider = parameter.marker.provider
                visit(overrides.get(provider, provider), (*trail, fn))
                continue
            spec = ArgSpec(
                parameter.name, parameter.annotation, parameter.default, parameter.marker.help
            )
            if get_origin(spec.annotation) is Literal and not all(
                isinstance(c, str) for c in get_args(spec.annotation)
            ):
                problems.append(f"argument {spec.name}: Literal choices must be strings")
            existing = specs.get(spec.name)
            if existing is None:
                specs[spec.name] = spec
            elif (existing.annotation, existing.default) != (spec.annotation, spec.default):
                problems.append(
                    f"argument {spec.name} is declared twice with different types or defaults"
                )

    for root in roots:
        visit(root, ())
    if "validate" in specs:
        problems.append("argument validate is reserved for --validate")
    if problems:
        raise GateDefinitionError(problems)
    return list(specs.values())


def parse_args(
    prog: str, specs: Sequence[ArgSpec], argv: Sequence[str]
) -> tuple[dict[str, Any], bool]:
    """The arguments' values and whether --validate was given; a bad command line prints usage
    and raises SystemExit(2), as argparse does."""
    parser = argparse.ArgumentParser(prog=prog, allow_abbrev=False)
    for spec in specs:
        choices = (
            [str(c) for c in get_args(spec.annotation)]
            if get_origin(spec.annotation) is Literal
            else None
        )
        if spec.default is EMPTY:
            parser.add_argument(spec.name, help=spec.help, choices=choices)
        elif spec.annotation is bool:
            parser.add_argument(
                f"--{spec.name.replace('_', '-')}",
                dest=spec.name,
                default=spec.default,
                help=spec.help,
                action=argparse.BooleanOptionalAction,
            )
        else:
            parser.add_argument(
                f"--{spec.name.replace('_', '-')}",
                dest=spec.name,
                default=spec.default,
                help=spec.help,
                choices=choices,
            )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="check the gate itself without credentials; nothing is observed",
    )
    namespace = parser.parse_args(list(argv))
    values: dict[str, Any] = {}
    for spec in specs:
        raw = getattr(namespace, spec.name)
        if spec.default is not EMPTY and raw is spec.default:
            values[spec.name] = raw
            continue
        try:
            values[spec.name] = TypeAdapter(spec.annotation).validate_python(raw)
        except ValidationError:
            parser.error(f"argument {spec.name}: invalid value {raw!r}")
    return values, namespace.validate


class Resolver:
    """Resolves parameters for one run: `Arg` values, and each provider once, lazily."""

    def __init__(
        self,
        values: Mapping[str, Any],
        overrides: Mapping[Callable[..., Any], Callable[..., Any]],
        stack: contextlib.ExitStack,
    ):
        self.values = values
        self.overrides = overrides
        self.stack = stack
        self.cache: dict[Callable[..., Any], Any] = {}
        self.cleanup_problems: list[str] = []

    def arguments(self, fn: Callable[..., Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for parameter in parameters_of(fn):
            if isinstance(parameter.marker, Arg):
                resolved[parameter.name] = self.values[parameter.name]
            else:
                resolved[parameter.name] = self.provide(parameter.marker.provider)
        return resolved

    def provide(self, provider: Callable[..., Any]) -> Any:
        if provider in self.cache:
            return self.cache[provider]
        actual = self.overrides.get(provider, provider)
        name = _name(provider)
        kwargs = self.arguments(actual)
        try:
            if inspect.isgeneratorfunction(actual):
                value = self._enter(name, contextlib.contextmanager(actual)(**kwargs))
            else:
                value = actual(**kwargs)
        except Unmet as exc:
            if exc.provider is None:
                exc.provider = name
            raise
        except (ProviderFailed, Propagate):
            raise
        except Exception as exc:
            raise ProviderFailed(name, type(exc).__name__) from None
        self.cache[provider] = value
        return value

    def _enter(self, name: str, manager: contextlib.AbstractContextManager[Any]) -> Any:
        value = manager.__enter__()

        def leave(*_exc_info: Any) -> bool:
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:
                self.cleanup_problems.append(f"provider {name} cleanup raised {type(exc).__name__}")
            return False

        self.stack.push(leave)
        return value
