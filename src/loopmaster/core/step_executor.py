"""Step execution logic with error recovery."""

from __future__ import annotations

import time
from typing import Any

from .context import Context
from .types import RecoveryAction, Step, StepResult


def execute_step(
    step: Step,
    context: Context,
    loop_def: Any,
    error_policy: Any,
    collector: Any = None,
) -> StepResult:
    """Execute a step with retry/backoff/skip/fallback logic.

    Args:
        step: The Step to execute.
        context: Current loop context.
        loop_def: The loop definition being executed.
        error_policy: ErrorPolicy for recovery decisions.
        collector: Optional MetricsCollector for retry recording.

    Returns:
        StepResult with success/failure status.
    """
    step_retries = max(step.retry, error_policy.retry)
    max_retries = step_retries if step_retries > 0 else 1

    for attempt in range(max_retries):
        result = step.execute(context.to_dict())

        if result.success:
            return result

        ctx_policy = getattr(context, "_current_error_policy", error_policy)
        recovery = ctx_policy.classify(result.error or "")

        if recovery == RecoveryAction.RETRY and attempt < max_retries - 1:
            if collector:
                collector.record_retry(loop_def.name, step.name)
            backoff = ctx_policy.backoff * (2**attempt)
            time.sleep(backoff)
            continue
        elif recovery == RecoveryAction.SKIP:
            return result
        elif recovery == RecoveryAction.FALLBACK and ctx_policy.fallback_model:
            fallback_step = Step(
                name=step.name,
                model=ctx_policy.fallback_model,
                prompt=step.prompt,
                input=step.input,
            )
            fallback_result = fallback_step.execute(context.to_dict())
            return fallback_result
        elif attempt < max_retries - 1:
            backoff = ctx_policy.backoff * (2**attempt)
            time.sleep(backoff)
            continue
        else:
            return result

    return StepResult(
        step_name=step.name,
        success=False,
        error="Max retries exceeded",
    )
