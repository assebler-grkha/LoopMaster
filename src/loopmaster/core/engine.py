"""LoopEngine — runtime that executes loop definitions.

The engine:
1. Receives a @Loop-decorated function (LoopDef)
2. Executes its body, intercepting Step() calls
3. Runs steps with error recovery, cost tracking, checkpoints
4. Supports resume from checkpoint via executed_step_names
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import Context
from .exceptions import BudgetExceededError
from .types import (
    Budget,
    CheckpointData,
    ErrorPolicy,
    InterruptionProtection,
    RecoveryAction,
    Step,
    StepResult,
)

_local = threading.local()


def _get_current_steps() -> list[Step] | None:
    return getattr(_local, "steps", None)


def _set_current_steps(steps: list[Step] | None) -> None:
    _local.steps = steps


@dataclass
class LoopRunResult:
    success: bool
    results: dict[str, StepResult]
    total_cost: float
    total_tokens: int
    steps_executed: list[str]
    error: Exception | None = None
    checkpoint_saved: bool = False


class LoopEngine:
    """Runtime engine for executing loop definitions."""

    def __init__(
        self,
        error_policy: ErrorPolicy | None = None,
        budget: Budget | None = None,
        interruption_protection: InterruptionProtection | None = None,
        checkpoint_dir: str | None = None,
    ) -> None:
        self.error_policy = error_policy or ErrorPolicy()
        self.budget = budget
        self.interruption_protection = interruption_protection
        self.checkpoint_dir = checkpoint_dir
        self._registry: dict[str, Any] = {}
        self._on_step_complete: Callable[[StepResult], None] | None = None

    def on_step_complete(
        self, callback: Callable[[StepResult], None]
    ) -> None:
        self._on_step_complete = callback

    def register(self, loop_def: Any) -> None:
        self._registry[loop_def.name] = loop_def

    def run(
        self,
        loop_def: Any,
        initial_context: dict[str, Any] | None = None,
        resume_checkpoint: CheckpointData | None = None,
    ) -> LoopRunResult:
        ctx = Context(initial_context or {})

        executed_steps: list[str] = []
        if resume_checkpoint:
            ctx = Context(resume_checkpoint.context_data)
            executed_steps = list(resume_checkpoint.executed_step_names)

        results: dict[str, StepResult] = {}
        total_cost = 0.0
        total_tokens = 0

        if resume_checkpoint:
            for step_name, sr in resume_checkpoint.completed_results.items():
                results[step_name] = StepResult(**sr)

        collected_steps: list[Step] = []
        _set_current_steps(collected_steps)
        try:
            ctx._loop_engine = self
            ctx._executed_steps = executed_steps
            ctx._results = results
            ctx._current_error_policy = self.error_policy
            loop_def.body(ctx)
        finally:
            _set_current_steps(None)

        for step in collected_steps:
            if step.name in executed_steps:
                continue

            if (
                self.budget
                and self.budget.max_steps is not None
                and len(executed_steps) >= self.budget.max_steps
            ):
                raise BudgetExceededError(
                    budget_limit=float(self.budget.max_steps),
                    spent=float(len(executed_steps)),
                )

            if (
                self.budget
                and self.budget.max_cost is not None
                and total_cost >= self.budget.max_cost
            ):
                raise BudgetExceededError(
                    budget_limit=self.budget.max_cost,
                    spent=total_cost,
                )

            result = self._execute_step(
                step=step,
                context=ctx,
                loop_def=loop_def,
            )

            executed_steps.append(step.name)
            results[step.name] = result
            ctx._executed_steps = executed_steps
            ctx._results = results

            if result.success and result.output is not None:
                ctx.merge(result.output)

            if result.cost:
                total_cost += result.cost
            if result.tokens_used:
                total_tokens += result.tokens_used

            if self._on_step_complete:
                self._on_step_complete(result)

            CheckpointData(
                loop_name=loop_def.name,
                loop_version=loop_def.version,
                loop_source_hash=loop_def.source_hash,
                step_index=len(executed_steps),
                context_data=ctx.to_dict(),
                completed_results={
                    k: {
                        "step_name": v.step_name,
                        "success": v.success,
                        "output": (
                            v.output.updates if v.output else None
                        ),
                        "error": v.error,
                        "tokens_used": v.tokens_used,
                        "cost": v.cost,
                        "duration_ms": v.duration_ms,
                    }
                    for k, v in results.items()
                },
                executed_step_names=list(executed_steps),
                recorded_responses={},
            )

        all_succeeded = all(r.success for r in results.values())

        return LoopRunResult(
            success=all_succeeded,
            results=results,
            total_cost=total_cost,
            total_tokens=total_tokens,
            steps_executed=executed_steps,
        )

    def _execute_step(
        self,
        step: Step,
        context: Context,
        loop_def: Any,
    ) -> StepResult:
        max_retries = step.retry if step.retry > 0 else 1
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                result = step.execute(context.to_dict())
                return result
            except Exception as e:
                last_error = e
                error_policy = getattr(
                    context,
                    "_current_error_policy",
                    self.error_policy,
                )
                recovery = error_policy.classify(type(e).__name__)

                if (
                    recovery == RecoveryAction.RETRY
                    and attempt < max_retries - 1
                ):
                    import asyncio

                    backoff = error_policy.backoff * (2**attempt)
                    if asyncio.get_event_loop().is_running():
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            pool.submit(time.sleep, backoff).result()
                    else:
                        time.sleep(backoff)
                    continue
                elif recovery == RecoveryAction.SKIP:
                    return StepResult(
                        step_name=step.name,
                        success=False,
                        error=str(e),
                    )
                elif (
                    recovery == RecoveryAction.FALLBACK
                    and error_policy.fallback_model
                ):
                    fallback_step = Step(
                        name=step.name,
                        model=error_policy.fallback_model,
                        prompt=step.prompt,
                        input=step.input,
                    )
                    try:
                        return fallback_step.execute(context.to_dict())
                    except Exception as fallback_e:
                        return StepResult(
                            step_name=step.name,
                            success=False,
                            error=str(fallback_e),
                        )
                else:
                    return StepResult(
                        step_name=step.name,
                        success=False,
                        error=str(e),
                    )

        return StepResult(
            step_name=step.name,
            success=False,
            error=str(last_error) if last_error else "Unknown error",
        )
