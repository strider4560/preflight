"""Operator guardrails: assertions a gate program runs before a script continues."""

import sys

# Gates import their own modules from the consumer's repository; leave no bytecode there.
sys.dont_write_bytecode = True

from preflight.check import (  # noqa: E402
    BoundCheck,
    Check,
    CheckCallError,
    UniqueList,
    check,
    unique_by,
)
from preflight.gate import Gate, Guards  # noqa: E402
from preflight.outcome import (  # noqa: E402
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
from preflight.params import Arg, Depends, Unmet  # noqa: E402
from preflight.probe import Probe  # noqa: E402
from preflight.runner import probe_now  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "Arg",
    "BoundCheck",
    "Check",
    "CheckCallError",
    "Depends",
    "Gate",
    "Guards",
    "Item",
    "NextStep",
    "Outcome",
    "Probe",
    "Status",
    "UniqueList",
    "Unmet",
    "check",
    "error",
    "fail",
    "ok",
    "outcome",
    "pending",
    "probe_now",
    "unique_by",
]
