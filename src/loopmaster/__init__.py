"""LoopMaster — Design, validate, and run AI agent loops."""

from loopmaster.core import (
    Budget,
    BudgetExceededError,
    CheckpointError,
    Conditional,
    Context,
    ErrorPolicy,
    InterruptedError,
    InterruptionProtection,
    Loop,
    LoopEngine,
    LoopError,
    Parallel,
    RecoveryAction,
    Step,
    StepError,
    StepInput,
    StepOutput,
    StepResult,
)

__version__ = "0.1.0"

__all__ = [
    "Loop",
    "Step",
    "Parallel",
    "Conditional",
    "StepInput",
    "StepOutput",
    "StepResult",
    "Budget",
    "InterruptionProtection",
    "RecoveryAction",
    "ErrorPolicy",
    "Context",
    "LoopError",
    "StepError",
    "CheckpointError",
    "BudgetExceededError",
    "InterruptedError",
    "LoopEngine",
    "LoopEvent",
]

from loopmaster.events import LoopEvent  # noqa: E402
