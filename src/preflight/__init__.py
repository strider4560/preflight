"""Operator guardrails: checks that stop a script and name the next step."""

from preflight.check import (
    Check,
    CheckInstance,
    IdentityRef,
    IdentitySection,
    Name,
    Requirement,
    Section,
    check,
    session_for,
)
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

__version__ = "0.1.0"

__all__ = [
    "Check",
    "CheckInstance",
    "IdentityRef",
    "IdentitySection",
    "Item",
    "Name",
    "NextStep",
    "Outcome",
    "Requirement",
    "Section",
    "Status",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "session_for",
]
