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
from preflight.gate import Gate, Guards, Run  # noqa: E402
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
from preflight.params import Arg, Depends, Unmet, observed  # noqa: E402
from preflight.probe import Probe  # noqa: E402
from preflight.runner import probe_now  # noqa: E402

__version__ = "0.2.0"

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
    "Run",
    "Status",
    "UniqueList",
    "Unmet",
    "check",
    "error",
    "fail",
    "observed",
    "ok",
    "outcome",
    "pending",
    "probe_now",
    "unique_by",
]
