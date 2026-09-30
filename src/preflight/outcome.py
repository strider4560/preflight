"""What one observation produced: statuses, items, and the operator's next step."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    PENDING = "pending"
    ERROR = "error"


# Worst first: an outcome takes the worst status among its blocking items.
SEVERITY = (Status.ERROR, Status.FAIL, Status.PENDING, Status.OK)


@dataclass(frozen=True)
class NextStep:
    do: str
    paste: str | None = None
    wait: str | None = None
    ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"do": self.do, "paste": self.paste, "wait": self.wait, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NextStep:
        return cls(data["do"], data.get("paste"), data.get("wait"), data.get("ref"))


@dataclass(frozen=True)
class Item:
    key: str | None
    status: Status
    observed: Any = None
    next_step: NextStep | None = None
    advisory: bool = False
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "observed": self.observed,
            "next_step": self.next_step.to_dict() if self.next_step else None,
            "advisory": self.advisory,
            "error_type": self.error_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Item:
        step = data.get("next_step")
        return cls(
            key=data["key"],
            status=Status(data["status"]),
            observed=data.get("observed"),
            next_step=NextStep.from_dict(step) if step else None,
            advisory=data.get("advisory", False),
            error_type=data.get("error_type"),
        )


@dataclass(frozen=True)
class Outcome:
    items: tuple[Item, ...]

    def __post_init__(self) -> None:
        items = tuple(self.items)
        object.__setattr__(self, "items", items)
        if not items:
            raise ValueError("an outcome needs at least one item")
        keys = [item.key for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("item keys must be unique")
        if len(items) > 1 and None in keys:
            raise ValueError("only a single item may be unnamed")

    @property
    def status(self) -> Status:
        blocking = {item.status for item in self.items if not item.advisory}
        for status in SEVERITY:
            if status in blocking:
                return status
        return Status.OK

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Outcome:
        return cls(tuple(Item.from_dict(item) for item in data["items"]))


def outcome(*items: Item) -> Outcome:
    return Outcome(items)


def ok(key: str | None = None, *, observed: Any = None, advisory: bool = False) -> Item:
    return Item(key, Status.OK, observed, None, advisory)


def fail(
    key: str | None = None,
    *,
    do: str,
    paste: str | None = None,
    wait: str | None = None,
    ref: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.FAIL, observed, NextStep(do, paste, wait, ref), advisory)


def pending(
    key: str | None = None,
    *,
    wait: str,
    do: str = "Nothing to do but wait.",
    paste: str | None = None,
    ref: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.PENDING, observed, NextStep(do, paste, wait, ref), advisory)


def error(
    key: str | None = None,
    *,
    do: str,
    paste: str | None = None,
    ref: str | None = None,
    error_type: str | None = None,
    observed: Any = None,
    advisory: bool = False,
) -> Item:
    return Item(key, Status.ERROR, observed, NextStep(do, paste, None, ref), advisory, error_type)
