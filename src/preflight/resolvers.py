# src/preflight/resolvers.py
"""References from a contract into the consumer's own files, and lazy SSM parameters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml
from hcl2 import SerializationOptions

SOURCES = ("tfvars", "yaml", "yaml_glob", "json", "ssm")
OPTIONS: dict[str, set[str]] = {
    "tfvars": {"key"},
    "yaml": {"path"},
    "yaml_glob": {"path"},
    "json": {"path"},
    "ssm": {"identity", "format", "path"},
}
COMMON = {"placeholder", "how"}
HCL_OPTIONS = SerializationOptions(
    strip_string_quotes=True, preserve_heredocs=False, with_comments=False
)
SSM_NAME = re.compile(r"^/[A-Za-z0-9_.\-/]+$")


class ResolveError(Exception):
    """A reference that cannot be resolved; the message names the problem."""


@dataclass(frozen=True)
class Reference:
    source: str
    target: str
    options: dict[str, Any]
    placeholder: tuple[Any, ...] = ()
    how: str | None = None

    def describe(self) -> str:
        if "key" in self.options:
            return f"{self.target} key {self.options['key']}"
        if self.options.get("path"):
            return f"{self.target} path {self.options['path']}"
        return self.target


@dataclass(frozen=True)
class LazySsm:
    name: str
    identity: str
    format: str = "text"
    path: str | None = None
    how: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "__lazy_ssm__": {
                "name": self.name,
                "identity": self.identity,
                "format": self.format,
                "path": self.path,
                "how": self.how,
            }
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LazySsm:
        return cls(**data["__lazy_ssm__"])


def parse_reference(table: Mapping[str, Any]) -> Reference | None:
    sources = [source for source in SOURCES if source in table]
    if not sources:
        return None
    if len(sources) > 1:
        raise ResolveError(f"a reference names more than one source: {', '.join(sources)}")
    source = sources[0]
    unknown = sorted(set(table) - OPTIONS[source] - COMMON - {source})
    if unknown:
        raise ResolveError(f"{source} reference has unknown keys: {', '.join(unknown)}")
    target = table[source]
    if not isinstance(target, str) or not target:
        raise ResolveError(f"{source} must be a non-empty string")
    if source == "tfvars" and not isinstance(table.get("key"), str):
        raise ResolveError("a tfvars reference needs key")
    if source == "ssm":
        if not SSM_NAME.match(target):
            raise ResolveError(f"{target!r} is not an SSM parameter name")
        if not isinstance(table.get("identity"), str):
            raise ResolveError("an ssm reference needs identity")
        if table.get("format", "text") not in ("text", "json"):
            raise ResolveError("format must be text or json")
    placeholder = table.get("placeholder", [])
    if not isinstance(placeholder, list):
        raise ResolveError("placeholder must be a list")
    how = table.get("how")
    if how is not None and not isinstance(how, str):
        raise ResolveError("how must be a string")
    options = {name: table[name] for name in OPTIONS[source] if name in table}
    return Reference(source, target, options, tuple(placeholder), how)


def dotted_get(value: Any, path: str | None) -> Any:
    if not path:
        return value
    for segment in path.split("."):
        if isinstance(value, list) and segment.isdigit():
            index = int(segment)
            if index >= len(value):
                raise ResolveError(f"index {segment} is out of range")
            value = value[index]
        elif isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            raise ResolveError(f"no {segment!r} at this path")
    return value


def _reject_expressions(value: Any, ref: Reference) -> None:
    if isinstance(value, str) and "${" in value:
        raise ResolveError(f"{ref.describe()} is an expression, not a literal")
    if isinstance(value, list):
        for element in value:
            _reject_expressions(element, ref)
    if isinstance(value, dict):
        for element in value.values():
            _reject_expressions(element, ref)


def apply_ssm_value(raw: str, lazy: LazySsm) -> Any:
    if lazy.format == "text":
        return dotted_get(raw, lazy.path) if lazy.path else raw
    try:
        value = json.loads(raw)
    except ValueError:
        raise ResolveError(f"{lazy.name} is not JSON") from None
    return dotted_get(value, lazy.path)


class Resolver:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.inputs: dict[str, str] = {}
        self._cache: dict[Path, Any] = {}

    def resolve(self, ref: Reference) -> Any:
        if ref.source == "ssm":
            return LazySsm(
                ref.target,
                ref.options["identity"],
                ref.options.get("format", "text"),
                ref.options.get("path"),
                ref.how,
            )
        if ref.source == "yaml_glob":
            return [
                {
                    "file": path.relative_to(self.root).as_posix(),
                    "value": dotted_get(self._load(path, "yaml"), ref.options.get("path")),
                }
                for path in sorted(p.resolve() for p in self.root.glob(ref.target) if p.is_file())
            ]
        document = self._load(self._path(ref.target), ref.source)
        if ref.source == "tfvars":
            key = ref.options["key"]
            if key not in document:
                raise ResolveError(f"{ref.target} has no variable {key}")
            value = document[key]
            _reject_expressions(value, ref)
            return value
        return dotted_get(document, ref.options.get("path"))

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ResolveError(f"{relative} is outside the repository")
        if not path.is_file():
            raise ResolveError(f"{relative} does not exist")
        return path

    def _load(self, path: Path, kind: str) -> Any:
        if path not in self._cache:
            raw = path.read_bytes()
            relative = path.relative_to(self.root).as_posix()
            self.inputs[relative] = hashlib.sha256(raw).hexdigest()
            try:
                text = raw.decode("utf-8")
                if kind == "tfvars":
                    data = hcl2.loads(text, serialization_options=HCL_OPTIONS)
                elif kind == "json":
                    data = json.loads(text)
                else:
                    data = yaml.safe_load(text)
            except Exception as exc:
                raise ResolveError(f"{relative} cannot be parsed ({type(exc).__name__})") from None
            self._cache[path] = data
        return self._cache[path]
