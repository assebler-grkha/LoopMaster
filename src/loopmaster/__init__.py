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
from loopmaster.executors import (
    BaseExecutor,
    HTTPExecutor,
    HTTPResult,
    MCPToolExecutor,
    MCPToolResult,
    ShellExecutor,
    ShellResult,
)

__version__ = "0.1.0"

__all__ = [
    "Loop",
    "Step",
    "Parallel",
    "Conditional",
    "BaseExecutor",
    "ShellExecutor",
    "ShellResult",
    "HTTPExecutor",
    "HTTPResult",
    "MCPToolExecutor",
    "MCPToolResult",
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
