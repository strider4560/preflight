"""Operator guardrails: assertions a gate program runs before a script continues."""

from preflight.check import BoundCheck, Check, CheckCallError, UniqueList, check, unique_by
from preflight.outcome import (
    Item,
    NextStep,
    Outcome,
    Status,
    error,
    fail,
    ok,
    outcome,
    pending,
)
from preflight.probe import Probe

__version__ = "0.1.0"

__all__ = [
    "BoundCheck",
    "Check",
    "CheckCallError",
    "Item",
    "NextStep",
    "Outcome",
    "Probe",
    "Status",
    "UniqueList",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "unique_by",
]
