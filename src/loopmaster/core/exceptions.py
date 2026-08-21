"""LoopMaster exceptions."""


class LoopError(Exception):
    """Base exception for all LoopMaster errors."""


class StepError(LoopError):
    """Raised when a step fails."""

    def __init__(
        self, step_name: str, reason: str, cause: Exception | None = None
    ):
        self.step_name = step_name
        self.reason = reason
        self.cause = cause
        super().__init__(f"Step '{step_name}' failed: {reason}")


class CheckpointError(LoopError):
    """Raised when checkpoint operations fail."""


class BudgetExceededError(LoopError):
    """Raised when budget limit is exceeded."""

    def __init__(self, budget_limit: float, spent: float):
        self.budget_limit = budget_limit
        self.spent = spent
        super().__init__(
            f"Budget exceeded: ${spent:.4f} / ${budget_limit:.4f}"
        )


class InterruptedError(LoopError):
    """Raised when loop is interrupted."""


class ReplayError(LoopError):
    """Raised when deterministic replay fails."""


class ExportError(LoopError):
    """Raised when export fails."""
