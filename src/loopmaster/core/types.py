"""Core types for LoopMaster DSL."""

from __future__ import annotations

import datetime
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecoveryAction(Enum):
    """Action to take after error recovery fails."""

    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    FALLBACK = "fallback"


@dataclass
class ErrorPolicy:
    """Policy for handling step errors."""

    retry: int = 2
    backoff: float = 1.0
    on_failure: RecoveryAction = RecoveryAction.ABORT
    fallback_model: str | None = None

    def classify(self, error_type: str) -> RecoveryAction:
        """Classify an error type and return the recovery action."""
        if error_type in ("RateLimitError", "TimeoutError"):
            return RecoveryAction.RETRY
        if error_type in ("ValidationError", "SchemaError"):
            return RecoveryAction.SKIP
        return self.on_failure


@dataclass
class Budget:
    """Budget constraints for a loop."""

    max_cost: float | None = None
    max_tokens: int | None = None
    max_steps: int | None = None

    @classmethod
    def from_string(cls, value: str) -> Budget:
        """Parse budget from string like '$5.00'."""
        if value.startswith("$"):
            return cls(max_cost=float(value[1:]))
        return cls(max_cost=float(value))


@dataclass
class InterruptionProtection:
    """Configuration for interruption detection and recovery."""

    enabled: bool = False
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 60.0
    pre_step_checkpoint: bool = True
    post_step_checkpoint: bool = True
    context_overflow_strategy: str = "compress_and_resume"
    max_resume_attempts: int = 3


@dataclass
class StepInput:
    """Immutable snapshot of context passed to a step."""

    _data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"StepInput has no attribute '{name}'") from None

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


@dataclass
class StepOutput:
    """Diff returned by a step. Runtime merges into context."""

    updates: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """Result of a step execution."""

    step_name: str
    success: bool
    output: Any = None
    error: str | None = None
    tokens_used: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0


@dataclass
class Step:
    """A step in a loop. Executes immediately when called (lazy execution)."""

    name: str
    tool: str | None = None
    model: str | None = None
    prompt: str | None = None
    input: Any = None
    retry: int = 1
    timeout: float | None = None
    on_error: ErrorPolicy | None = None

    _result: StepResult | None = field(default=None, repr=False)
    _engine_callback: Callable[..., Any] | None = field(
        default=None, repr=False
    )

    def execute(self, ctx_data: dict[str, Any]) -> StepResult:
        """Execute the step. Called by the engine runtime."""
        import time

        start = time.monotonic()
        try:
            if self._engine_callback:
                output = self._engine_callback(self, ctx_data)
            else:
                output = self._execute_default(ctx_data)
            duration = (time.monotonic() - start) * 1000
            result = StepResult(
                step_name=self.name,
                success=True,
                output=output,
                duration_ms=duration,
            )
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            result = StepResult(
                step_name=self.name,
                success=False,
                error=str(exc),
                duration_ms=duration,
            )
        self._result = result
        return result

    def _execute_default(self, ctx_data: dict[str, Any]) -> Any:
        """Default execution: return input or prompt-resolved string."""
        if self.prompt:
            return self.prompt.format(**ctx_data)
        return self.input


@dataclass
class Parallel:
    """Execute multiple steps concurrently."""

    steps: list[Step] = field(default_factory=list)

    def __init__(self, *steps: Step):
        self.steps = list(steps)


def _get_source_hash(func: Callable[..., Any]) -> str:
    """Compute hash of function source code for checkpoint integrity."""
    try:
        source = textwrap.dedent(inspect.getsource(func))
        return hashlib.sha256(source.encode()).hexdigest()[:16]
    except (OSError, TypeError):
        return "unknown"


@dataclass
class CheckpointData:
    """Checkpoint state for loop resume. Stores data only, no code."""

    loop_name: str
    loop_version: str
    loop_source_hash: str
    step_index: int
    context_data: dict[str, Any]
    completed_results: dict[str, Any]
    executed_step_names: list[str]
    recorded_responses: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = (
                datetime.datetime.now(datetime.UTC).isoformat()
            )


@dataclass
class LoopDef:
    """Parsed loop definition (internal representation of @Loop decorator)."""

    name: str
    version: str
    body: Callable[..., Any]
    agent: str | None = None
    budget: Budget | None = None
    interruption_protection: InterruptionProtection | None = None
    source_hash: str = ""

    def __post_init__(self) -> None:
        if not self.source_hash:
            self.source_hash = _get_source_hash(self.body)


def Loop(  # noqa: N802
    name: str,
    version: str = "0.1.0",
    agent: str | None = None,
    budget: Budget | str | None = None,
    interruption_protection: InterruptionProtection | None = None,
) -> Callable[..., Any]:
    """Decorator that registers a function as a loop definition."""

    if isinstance(budget, str):
        budget = Budget.from_string(budget)

    def decorator(func: Callable[..., Any]) -> LoopDef:
        loop_def = LoopDef(
            name=name,
            version=version,
            body=func,
            agent=agent,
            budget=budget,
            interruption_protection=interruption_protection,
        )
        func._loop_def = loop_def  # type: ignore[attr-defined]
        return loop_def

    return decorator
