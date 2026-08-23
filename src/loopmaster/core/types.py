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

    def to_dict(self) -> str:
        """Serialize to string for YAML export."""
        return self.value


@dataclass
class ErrorPolicy:
    """Policy for handling step errors."""

    retry: int = 2
    backoff: float = 1.0
    on_failure: RecoveryAction = RecoveryAction.ABORT
    fallback_model: str | None = None

    def classify(self, error_type: str) -> RecoveryAction:
        """Classify an error type or message and return the recovery action."""
        err_str = str(error_type)
        if (
            "RateLimitError" in err_str
            or "429" in err_str
            or "Too Many Requests" in err_str
            or "TimeoutError" in err_str
            or "timed out" in err_str.lower()
            or "Timeout" in err_str
        ):
            return RecoveryAction.RETRY
        if "ValidationError" in err_str or "SchemaError" in err_str:
            return RecoveryAction.SKIP
        return self.on_failure

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        d: dict[str, Any] = {"retry": self.retry, "backoff": self.backoff}
        if self.on_failure != RecoveryAction.ABORT:
            d["on_failure"] = self.on_failure.value
        if self.fallback_model:
            d["fallback_model"] = self.fallback_model
        return d


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        d: dict[str, Any] = {}
        if self.max_cost is not None:
            d["max_cost"] = self.max_cost
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.max_steps is not None:
            d["max_steps"] = self.max_steps
        return d


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        d: dict[str, Any] = {}
        if self.enabled:
            d["enabled"] = True
        if self.heartbeat_interval != 30.0:
            d["heartbeat_interval"] = self.heartbeat_interval
        if self.heartbeat_timeout != 60.0:
            d["heartbeat_timeout"] = self.heartbeat_timeout
        if not self.pre_step_checkpoint:
            d["pre_step_checkpoint"] = False
        if not self.post_step_checkpoint:
            d["post_step_checkpoint"] = False
        if self.context_overflow_strategy != "compress_and_resume":
            d["context_overflow_strategy"] = self.context_overflow_strategy
        if self.max_resume_attempts != 3:
            d["max_resume_attempts"] = self.max_resume_attempts
        return d


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


def resolve_prompt(template: str, ctx_data: dict[str, Any]) -> str:
    """Resolve variables like {var} or {{var}} from context without failing on JSON braces."""
    import re

    if not template:
        return template

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in ctx_data:
            return str(ctx_data[key])
        return match.group(0)

    return re.sub(r"\{\{?([a-zA-Z_]\w*)\}?\}", _repl, template)


@dataclass
class StepResult:
    """Result of a step execution."""

    step_name: str
    success: bool
    output: Any = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_used: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0
    model: str | None = None

    def __post_init__(self) -> None:
        if self.tokens_used == 0 and (self.input_tokens > 0 or self.output_tokens > 0):
            self.tokens_used = self.input_tokens + self.output_tokens


@dataclass
class Step:
    """A step in a loop. Executes immediately when called (lazy execution)."""

    name: str
    tool: str | None = None
    model: str | None = None
    prompt: str | None = None
    input: Any = None
    retry: int | None = None
    timeout: float | None = None
    on_error: ErrorPolicy | None = None

    _result: StepResult | None = field(default=None, repr=False)
    _engine_callback: Callable[..., Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from .engine import _get_current_steps

        steps = _get_current_steps()
        if steps is not None:
            steps.append(self)

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
            tokens = getattr(output, "_tokens", 0)
            cost = getattr(output, "_cost", 0.0)
            result = StepResult(
                step_name=self.name,
                success=True,
                output=output,
                tokens_used=tokens,
                cost=cost,
                duration_ms=duration,
                model=self.model,
            )
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            result = StepResult(
                step_name=self.name,
                success=False,
                error=str(exc),
                duration_ms=duration,
                model=self.model,
            )
        self._result = result
        return result

    def _execute_default(self, ctx_data: dict[str, Any]) -> Any:
        """Default execution: return input or prompt-resolved string."""
        if self.prompt:
            return resolve_prompt(self.prompt, ctx_data)
        return self.input

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        d: dict[str, Any] = {"name": self.name}
        if self.tool:
            d["tool"] = self.tool
        if self.model:
            d["model"] = self.model
        if self.prompt:
            d["prompt"] = self.prompt
        if self.input is not None:
            d["input"] = self.input
        if self.retry is not None:
            d["retry"] = self.retry
        if self.timeout is not None:
            d["timeout"] = self.timeout
        if self.on_error:
            d["on_error"] = self.on_error.to_dict()
        return d


@dataclass
class Parallel:
    """Execute multiple steps concurrently."""

    steps: list[Step] = field(default_factory=list)

    def __init__(self, *steps: Step):
        self.steps = list(steps)

    def __post_init__(self) -> None:
        from .engine import _get_current_steps

        current_steps = _get_current_steps()
        if current_steps is not None:
            contained_ids = {id(s) for s in self.steps}
            current_steps[:] = [s for s in current_steps if id(s) not in contained_ids]
            current_steps.append(self)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        return {"parallel": [s.to_dict() for s in self.steps]}


@dataclass
class Conditional:
    """A conditional branching construct in a loop."""

    condition: Callable[[Any], bool] | str | bool
    then_steps: list[Any] = field(default_factory=list)
    else_steps: list[Any] = field(default_factory=list)
    name: str = ""

    def __post_init__(self) -> None:
        from .engine import _get_current_steps

        current_steps = _get_current_steps()
        if current_steps is not None:
            contained_ids = {id(s) for s in (*self.then_steps, *self.else_steps)}
            current_steps[:] = [s for s in current_steps if id(s) not in contained_ids]
            current_steps.append(self)

    def evaluate(self, ctx: Any) -> bool:
        """Evaluate the condition against current execution context."""
        from .condition import evaluate_condition

        return evaluate_condition(self.condition, ctx)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        cond_repr = (
            getattr(self.condition, "__name__", "<callable>")
            if callable(self.condition)
            else str(self.condition)
        )
        d: dict[str, Any] = {
            "condition": cond_repr,
            "then": [s.to_dict() for s in self.then_steps],
        }
        if self.else_steps:
            d["else"] = [s.to_dict() for s in self.else_steps]
        if self.name:
            d["name"] = self.name
        return {"conditional": d}


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
            self.created_at = datetime.datetime.now(datetime.UTC).isoformat()


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
    _collected_steps: list[Step] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.source_hash:
            self.source_hash = _get_source_hash(self.body)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for YAML export."""
        d: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.agent:
            d["agent"] = self.agent
        if self.budget:
            b = self.budget.to_dict()
            if b:
                d["budget"] = b
        if self.interruption_protection:
            ip = self.interruption_protection.to_dict()
            if ip:
                d["interruption_protection"] = ip
        if self.source_hash:
            d["source_hash"] = self.source_hash
        return d


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
