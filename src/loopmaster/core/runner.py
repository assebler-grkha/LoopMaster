"""Execution orchestration and block runner helpers for LoopEngine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .context import Context
from .exceptions import BudgetExceededError, InterruptedError, LoopError
from .state import (
    apply_step_result,
    check_budget_limits,
    emit_step_completed_event,
    make_checkpoint,
)
from .step_executor import execute_step
from .types import CheckpointData, Step, StepResult

logger = logging.getLogger("loopmaster.core.runner")


@dataclass
class BlockExecContext:
    """Encapsulates runtime state and objects for step block execution."""

    engine: Any
    loop_def: Any
    ctx: Context
    executed_steps: list[str]
    results: dict[str, StepResult]
    job_id: str
    total_steps: int
    totals: list[Any]


def execute_single_step(bctx: BlockExecContext, step: Step) -> tuple[float, int]:
    """Execute a single step, emit step_started event, and apply result."""
    step_idx = len(bctx.executed_steps)
    if bctx.engine.event_emitter:
        bctx.engine.event_emitter.emit(
            job_id=bctx.job_id,
            event_type="step_started",
            step_index=step_idx,
            payload={
                "step_name": step.name,
                "model": step.model,
                "attempt": 1,
                "reset_buffer": True,
            },
        )

    result = execute_step(
        step=step,
        context=bctx.ctx,
        loop_def=bctx.loop_def,
        error_policy=bctx.engine.error_policy,
        collector=bctx.engine._collector,
        llm_client=bctx.engine._llm_client,
        cost_tracker=bctx.engine._cost_tracker,
        event_emitter=bctx.engine.event_emitter,
        job_id=bctx.job_id,
        step_index=step_idx,
        total_steps=bctx.total_steps,
    )

    return apply_step_result(
        loop_def=bctx.loop_def,
        step=step,
        result=result,
        ctx=bctx.ctx,
        executed_steps=bctx.executed_steps,
        results=bctx.results,
        heartbeat=bctx.engine._heartbeat,
        collector=bctx.engine._collector,
        cost_tracker=bctx.engine._cost_tracker,
        on_step_complete=bctx.engine._on_step_complete,
    )


def run_conditional_block(bctx: BlockExecContext, conditional: Any) -> None:
    """Evaluate or stick to a conditional branch and execute its sub-blocks."""
    executed_set = set(bctx.executed_steps)

    then_names = {s.name for s in getattr(conditional, "then_steps", []) if hasattr(s, "name")}
    else_names = {s.name for s in getattr(conditional, "else_steps", []) if hasattr(s, "name")}

    if then_names and any(name in executed_set for name in then_names):
        branch_chosen, cond_res = "then", True
    elif else_names and any(name in executed_set for name in else_names):
        branch_chosen, cond_res = "else", False
    else:
        cond_res = conditional.evaluate(bctx.ctx)
        branch_chosen = "then" if cond_res else "else"

    cond_name = getattr(conditional, "name", "") or "conditional"
    if bctx.engine.event_emitter:
        bctx.engine.event_emitter.emit(
            job_id=bctx.job_id,
            event_type="branch_selected",
            step_index=len(bctx.executed_steps),
            payload={"name": cond_name, "branch": branch_chosen, "condition_result": cond_res},
        )

    from ..telemetry import get_tracer

    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"conditional.{cond_name}",
        attributes={"loopmaster.branch": branch_chosen, "loopmaster.condition_result": cond_res},
    ):
        selected = conditional.then_steps if branch_chosen == "then" else conditional.else_steps
        for sub_block in selected:
            run_step_block(bctx, sub_block)


def run_step_block(bctx: BlockExecContext, block: Any) -> None:
    """Execute a single step or recursive block (Conditional)."""
    ip = bctx.engine.interruption_protection

    if isinstance(block, Step):
        if block.name in bctx.executed_steps:
            return

        if bctx.engine._cancel_event and bctx.engine._cancel_event.is_set():
            raise InterruptedError("Loop cancelled by user")

        check_budget_limits(
            bctx.engine.budget,
            bctx.loop_def,
            bctx.ctx,
            bctx.executed_steps,
            bctx.results,
            bctx.totals[0],
            bctx.totals[1],
            bctx.engine._heartbeat,
            bctx.engine.checkpoint_dir,
        )
        if ip and ip.enabled and ip.pre_step_checkpoint:
            bctx.engine._last_checkpoint = make_checkpoint(
                bctx.loop_def,
                bctx.ctx,
                bctx.executed_steps,
                bctx.results,
                bctx.engine.checkpoint_dir,
            )

        step_idx = len(bctx.executed_steps)
        cost_added, tokens_added = execute_single_step(bctx, block)
        bctx.totals[0] += cost_added
        bctx.totals[1] += tokens_added

        emit_step_completed_event(
            bctx.engine.event_emitter,
            bctx.job_id,
            block,
            bctx.results[block.name],
            (step_idx, bctx.total_steps),
            (bctx.totals[0], bctx.totals[1]),
        )

        if (ip and ip.enabled and ip.post_step_checkpoint) or (not ip or not ip.enabled):
            bctx.engine._last_checkpoint = make_checkpoint(
                bctx.loop_def,
                bctx.ctx,
                bctx.executed_steps,
                bctx.results,
                bctx.engine.checkpoint_dir,
            )
    elif hasattr(block, "then_steps"):
        run_conditional_block(bctx, block)


def execute_traced_loop(
    engine: Any,
    loop_def: Any,
    ctx: Context,
    state: tuple[list[str], dict[str, StepResult]],
    job_meta: tuple[str, int, CheckpointData | None],
) -> tuple[float, int, bool]:
    """Wrap loop execution inside root OTel span with error handling and interruption capture."""
    executed_steps, results = state
    effective_job_id, resume_cnt, resume_checkpoint = job_meta
    from ..telemetry import SpanStatusCode, get_tracer

    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"loop.{loop_def.name}",
        attributes={
            "loopmaster.loop.name": loop_def.name,
            "loopmaster.loop.version": loop_def.version,
            "loopmaster.job_id": effective_job_id,
            "loopmaster.resume_count": resume_cnt,
        },
    ) as loop_span:
        try:
            if resume_checkpoint is None:
                loop_def._collected_steps = None
            total_cost, total_tokens = engine._run_steps_loop(
                loop_def, ctx, executed_steps, results, effective_job_id
            )
            loop_span.set_attribute("loopmaster.total_cost", total_cost)
            loop_span.set_attribute("loopmaster.total_tokens", total_tokens)
            loop_span.set_attribute("loopmaster.steps_count", len(executed_steps))
            return total_cost, total_tokens, False
        except (BudgetExceededError, InterruptedError, LoopError) as exc:
            loop_span.set_status(SpanStatusCode.ERROR, str(exc))
            engine._handle_loop_error(effective_job_id, loop_def.name, len(executed_steps), exc)
            raise
        except Exception as exc:
            loop_span.set_status(SpanStatusCode.ERROR, str(exc))
            engine._handle_loop_error(effective_job_id, loop_def.name, len(executed_steps), exc)
            engine._last_checkpoint = make_checkpoint(
                loop_def, ctx, executed_steps, results, engine.checkpoint_dir
            )
            raise InterruptedError(f"Loop '{loop_def.name}' interrupted: {exc}") from exc
