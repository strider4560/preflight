"""Contracts: expected values as literals or references, checked before anything is observed."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from preflight.check import field_problems
from preflight.identity import Identity
from preflight.outcome import NextStep
from preflight.resolvers import LazySsm, Reference, ResolveError, Resolver, parse_reference

NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
TOP_LEVEL = {"schema_version", "scope", "environment", "identities"}
SCOPES = ("environment", "repository")


class ContractError(Exception):
    """The contract cannot be used; `problems` lists everything wrong with it."""

    def __init__(self, path: Path, problems: list[str]):
        self.path = path
        self.problems = list(problems)
        super().__init__(f"{path}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class Placeholder:
    path: str
    owner: str
    field: str
    reference: Reference

    @property
    def id(self) -> str:
        return f"contract.placeholder[{self.path}]"

    def next_step(self) -> NextStep:
        options = self.reference.options
        key = options.get("key") or options.get("path") or self.field
        return NextStep(do=f"Fill `{key}` in `{self.reference.target}`.", paste=self.reference.how)


def _contains_lazy(value: Any) -> bool:
    if isinstance(value, LazySsm):
        return True
    if isinstance(value, dict):
        return any(_contains_lazy(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_lazy(v) for v in value)
    return False


@dataclass
class Contract:
    path: Path
    root: Path
    scope: str
    environment: str | None
    identity_data: dict[str, dict[str, Any]]
    sections: dict[str, dict[str, Any]]
    placeholders: list[Placeholder]
    inputs: dict[str, str]

    def identity(self, alias: str) -> Identity:
        return Identity.model_validate(self.identity_data[alias])

    def ready_identities(self) -> dict[str, Identity]:
        blocked = {p.owner for p in self.placeholders}
        return {
            alias: self.identity(alias)
            for alias in self.identity_data
            if f"identities.{alias}" not in blocked
        }

    def placeholder_fields(self, owner: str) -> set[str]:
        return {p.field for p in self.placeholders if p.owner == owner}

    def lazy_fields(self, section: str) -> set[str]:
        return {name for name, value in self.sections[section].items() if _contains_lazy(value)}

    def template_values(self, section: str) -> dict[str, Any]:
        skip = self.placeholder_fields(section) | self.lazy_fields(section)
        values = {
            name: value
            for name, value in self.sections[section].items()
            if name not in skip and isinstance(value, (str, int, float, bool))
        }
        values["environment"] = self.environment or ""
        return values

    def changed_inputs(self) -> list[str]:
        changed = []
        for relative, digest in self.inputs.items():
            try:
                current = hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
            except OSError:
                current = None
            if current != digest:
                changed.append(relative)
        return changed


def repo_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except OSError:
        raise ContractError(start, ["git is not available"]) from None
    if result.returncode != 0:
        raise ContractError(start, ["not inside a git work tree"])
    return Path(result.stdout.strip()).resolve()


class _Loader:
    def __init__(self, resolver: Resolver):
        self.resolver = resolver
        self.placeholders: list[Placeholder] = []
        self.problems: list[str] = []
        self.failed: set[tuple[str, str]] = set()

    def table(self, table: dict[str, Any], path: str, owner: str) -> dict[str, Any]:
        return {name: self.value(v, f"{path}.{name}", owner, name) for name, v in table.items()}

    def value(self, value: Any, path: str, owner: str, field: str) -> Any:
        if isinstance(value, dict):
            try:
                ref = parse_reference(value)
            except ResolveError as exc:
                self.problems.append(f"{path}: {exc}")
                return None
            if ref is None:
                return {k: self.value(v, f"{path}.{k}", owner, field) for k, v in value.items()}
            try:
                resolved = self.resolver.resolve(ref)
            except ResolveError as exc:
                self.problems.append(f"{path}: {exc}")
                self.failed.add((owner, field))
                return None
            except (OSError, ValueError) as exc:
                self.problems.append(
                    f"{path}: {ref.describe()} cannot be read ({type(exc).__name__})"
                )
                self.failed.add((owner, field))
                return None
            if not isinstance(resolved, LazySsm):
                self.find_placeholders(resolved, path, owner, field, ref)
            return resolved
        if isinstance(value, list):
            return [self.value(v, f"{path}[{i}]", owner, field) for i, v in enumerate(value)]
        return value

    def find_placeholders(
        self, value: Any, path: str, owner: str, field: str, ref: Reference
    ) -> None:
        if isinstance(value, list):
            for i, element in enumerate(value):
                self.find_placeholders(element, f"{path}[{i}]", owner, field, ref)
        elif isinstance(value, dict):
            for key, element in value.items():
                self.find_placeholders(element, f"{path}.{key}", owner, field, ref)
        elif any(value == p and type(value) is type(p) for p in ref.placeholder):
            self.placeholders.append(Placeholder(path, owner, field, ref))


def load_contract(path: Path) -> Contract:
    path = path.resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(path, [f"cannot be read ({exc.strerror})"]) from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ContractError(path, [f"not valid TOML: {exc}"]) from None
    root = repo_root(path.parent)
    resolver = Resolver(root)
    resolver.inputs[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    loader = _Loader(resolver)
    problems = loader.problems

    if data.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    scope = data.get("scope", "environment")
    environment = data.get("environment")
    if scope not in SCOPES:
        problems.append("scope must be environment or repository")
    elif scope == "environment" and not (isinstance(environment, str) and NAME.match(environment)):
        problems.append("environment is required, lowercase letters, digits, - and _")
    elif scope == "repository" and environment is not None:
        problems.append("a repository contract has no environment")
    for key, value in data.items():
        if key not in TOP_LEVEL and not isinstance(value, dict):
            problems.append(f"{key}: unknown top-level key")

    identity_data: dict[str, dict[str, Any]] = {}
    identities = data.get("identities", {})
    if not isinstance(identities, dict):
        problems.append("identities must be a table")
        identities = {}
    for alias, spec in identities.items():
        where = f"identities.{alias}"
        if not NAME.match(alias) or not isinstance(spec, dict):
            problems.append(f"{where}: an identity is a table named in lowercase")
            continue
        resolved = loader.table(spec, where, where)
        if any(_contains_lazy(v) for v in resolved.values()):
            problems.append(f"{where}: an identity cannot use an ssm reference")
            continue
        skip = {p.field for p in loader.placeholders if p.owner == where}
        skip |= {field for owner, field in loader.failed if owner == where}
        if skip and (("role" in resolved) == ("permission_set" in resolved)):
            problems.append(f"{where}: give exactly one of role or permission_set")
        problems.extend(f"{where}.{p}" for p in field_problems(Identity, resolved, skip))
        identity_data[alias] = resolved

    sections: dict[str, dict[str, Any]] = {}
    for name, table in data.items():
        if name in TOP_LEVEL or not isinstance(table, dict):
            continue
        if not NAME.match(name):
            problems.append(f"[{name}]: section names are lowercase letters, digits, - and _")
            continue
        sections[name] = loader.table(table, name, name)
        if isinstance(sections[name].get("identity"), LazySsm):
            problems.append(f"{name}.identity: cannot be an ssm reference")
        for value in sections[name].values():
            for lazy in _lazy_values(value):
                if lazy.identity not in identities:
                    problems.append(
                        f"[{name}]: ssm {lazy.name} names unknown identity {lazy.identity}"
                    )

    if problems:
        raise ContractError(path, problems)
    return Contract(
        path=path,
        root=root,
        scope=scope,
        environment=environment if scope == "environment" else None,
        identity_data=identity_data,
        sections=sections,
        placeholders=loader.placeholders,
        inputs=resolver.inputs,
    )


def _lazy_values(value: Any):
    if isinstance(value, LazySsm):
        yield value
    elif isinstance(value, dict):
        for element in value.values():
            yield from _lazy_values(element)
    elif isinstance(value, list):
        for element in value:
            yield from _lazy_values(element)
