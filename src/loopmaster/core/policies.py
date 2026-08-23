"""Error recovery, budget constraints, and interruption policies for LoopMaster."""

from __future__ import annotations

from dataclasses import dataclass
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
            or "502" in err_str
            or "503" in err_str
            or "504" in err_str
            or "ConnectionReset" in err_str
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
