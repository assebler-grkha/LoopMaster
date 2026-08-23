"""Execution state and checkpoint helpers for LoopEngine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .context import Context
from .exceptions import BudgetExceededError, InterruptedError
from .heartbeat import HeartbeatState, is_interrupted, ping_heartbeat
from .types import Budget, CheckpointData, Step, StepOutput, StepResult

logger = logging.getLogger("loopmaster.core.state")


def init_run_state(
    initial_context: dict[str, Any] | None,
    resume_checkpoint: CheckpointData | None,
) -> tuple[Context, list[str], dict[str, StepResult], int]:
    """Initialize execution context and state from scratch or checkpoint."""
    ctx = Context(initial_context or {})
    executed_steps: list[str] = []
    results: dict[str, StepResult] = {}
    resume_count = 0

    if resume_checkpoint:
        ctx = Context(resume_checkpoint.context_data)
        executed_steps = list(resume_checkpoint.executed_step_names)
        resume_count = 1
        for step_name, sr in resume_checkpoint.completed_results.items():
            results[step_name] = StepResult(**sr)

    return ctx, executed_steps, results, resume_count


def make_checkpoint(
    loop_def: Any,
    ctx: Context,
    executed_steps: list[str],
    results: dict[str, StepResult],
    checkpoint_dir: str | Path | None = None,
) -> CheckpointData:
    """Create and persist a CheckpointData snapshot."""
    cp = CheckpointData(
        loop_name=loop_def.name,
        loop_version=loop_def.version,
        loop_source_hash=loop_def.source_hash,
        step_index=len(executed_steps),
        context_data=ctx.to_dict(),
        completed_results={
            k: {
                "step_name": v.step_name,
                "success": v.success,
                "output": (v.output.updates if isinstance(v.output, StepOutput) else v.output),
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

    if checkpoint_dir:
        try:
            from ..checkpoint import CheckpointManager

            mgr = CheckpointManager(checkpoint_dir)
            mgr.save(cp)
        except Exception as exc:
            logger.warning("Failed to save checkpoint: %s", exc)

    return cp


def check_budget_limits(
    budget: Budget | None,
    loop_def: Any,
    ctx: Context,
    executed_steps: list[str],
    results: dict[str, StepResult],
    total_cost: float,
    total_tokens: int = 0,
    heartbeat: HeartbeatState | None = None,
    checkpoint_dir: str | Path | None = None,
) -> None:
    """Check heartbeat and budget constraints, saving checkpoint on violation."""
    if heartbeat and is_interrupted(heartbeat):
        make_checkpoint(loop_def, ctx, executed_steps, results, checkpoint_dir)
        raise InterruptedError(f"Loop '{loop_def.name}' interrupted — heartbeat timeout")

    if budget and budget.max_steps is not None and len(executed_steps) >= budget.max_steps:
        make_checkpoint(loop_def, ctx, executed_steps, results, checkpoint_dir)
        raise BudgetExceededError(
            budget_limit=float(budget.max_steps),
            spent=float(len(executed_steps)),
            unit="",
        )

    if budget and budget.max_cost is not None and total_cost >= budget.max_cost:
        make_checkpoint(loop_def, ctx, executed_steps, results, checkpoint_dir)
        raise BudgetExceededError(
            budget_limit=budget.max_cost,
            spent=total_cost,
        )

    if budget and budget.max_tokens is not None and total_tokens >= budget.max_tokens:
        make_checkpoint(loop_def, ctx, executed_steps, results, checkpoint_dir)
        raise BudgetExceededError(
            budget_limit=float(budget.max_tokens),
            spent=float(total_tokens),
            unit="tokens",
        )


def apply_step_result(
    loop_def: Any,
    step: Step,
    result: StepResult,
    ctx: Context,
    executed_steps: list[str],
    results: dict[str, StepResult],
    heartbeat: HeartbeatState | None = None,
    collector: Any = None,
    cost_tracker: Any = None,
    on_step_complete: Any = None,
) -> tuple[float, int]:
    """Apply step result to execution state, metrics, and cost tracker."""
    executed_steps.append(step.name)
    results[step.name] = result
    ctx._executed_steps = executed_steps
    ctx._results = results

    if result.success and result.output is not None:
        if isinstance(result.output, StepOutput):
            ctx.merge(result.output.updates)
        elif isinstance(result.output, dict):
            ctx.merge(result.output)
        else:
            output_val = getattr(result.output, "content", result.output)
            ctx._data[step.name] = output_val

    if heartbeat:
        ping_heartbeat(heartbeat)

    if collector:
        collector.record_step(
            loop_name=loop_def.name,
            step_name=step.name,
            cost=result.cost,
            tokens=result.tokens_used,
            duration_ms=result.duration_ms,
            success=result.success,
        )

    model_used = result.model or step.model
    if cost_tracker and model_used:
        in_tok = result.input_tokens
        out_tok = result.output_tokens
        if in_tok == 0 and out_tok == 0 and result.tokens_used > 0:
            in_tok = result.tokens_used
        recorded_cost = cost_tracker.record(
            model=model_used,
            input_tokens=in_tok,
            output_tokens=out_tok,
            step_name=step.name,
        )
        if not result.cost:
            result.cost = recorded_cost

    cost_added = result.cost or 0.0
    tokens_added = result.tokens_used or 0

    if on_step_complete:
        on_step_complete(result)

    return cost_added, tokens_added
