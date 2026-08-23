"""LoopEngine — runtime that executes loop definitions.

The engine:
1. Receives a @Loop-decorated function (LoopDef)
2. Executes its body, intercepting Step() calls
3. Runs steps with error recovery, cost tracking, checkpoints
4. Supports resume from checkpoint via executed_step_names
5. Interruption protection via heartbeats and pre/post checkpoints
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..cost.tracker import CostTracker
from ..events import EventEmitter
from ..metrics.collector import MetricsCollector
from .context import Context
from .exceptions import BudgetExceededError, InterruptedError, LoopError
from .heartbeat import (
    HeartbeatState,
    start_heartbeat,
    stop_heartbeat,
)
from .state import (
    apply_step_result,
    check_budget_limits,
    init_run_state,
    make_checkpoint,
)
from .step_executor import execute_step
from .types import (
    Budget,
    CheckpointData,
    ErrorPolicy,
    InterruptionProtection,
    Step,
    StepOutput,
    StepResult,
)

logger = logging.getLogger(__name__)

_local = threading.local()


def _get_current_steps() -> list[Step] | None:
    return getattr(_local, "steps", None)


def _set_current_steps(steps: list[Step] | None) -> None:
    _local.steps = steps


@dataclass
class LoopRunResult:
    """Result of a loop execution."""

    success: bool
    results: dict[str, StepResult]
    total_cost: float
    total_tokens: int
    steps_executed: list[str]
    error: str | None = None
    interrupted: bool = False
    resume_count: int = 0
    last_checkpoint: CheckpointData | None = None
    checkpoint_saved: bool = False


class LoopEngine:
    """Executes a loop definition with error recovery, cost tracking, and checkpoints."""

    def __init__(
        self,
        error_policy: ErrorPolicy | None = None,
        budget: Budget | None = None,
        interruption_protection: InterruptionProtection | None = None,
        checkpoint_dir: str | Path | None = None,
        metrics_collector: MetricsCollector | None = None,
        cost_tracker: CostTracker | None = None,
        llm_client: Any = None,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self.error_policy = error_policy or ErrorPolicy()
        self.budget = budget
        self.interruption_protection = interruption_protection
        self.checkpoint_dir = checkpoint_dir
        self._collector = metrics_collector
        self._cost_tracker = cost_tracker
        self._llm_client = llm_client
        self.event_emitter = event_emitter
        self._registry: dict[str, Any] = {}
        self._on_step_complete: Callable[[StepResult], None] | None = None
        self._heartbeat: HeartbeatState | None = None
        self._last_checkpoint: CheckpointData | None = None
        self._resume_count: int = 0

    def on_step_complete(self, callback: Callable[[StepResult], None]) -> None:
        self._on_step_complete = callback

    def register(self, loop_def: Any) -> None:
        self._registry[loop_def.name] = loop_def

    def _collect_steps_from_loop(
        self,
        loop_def: Any,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
    ) -> list[Step]:
        ctx._loop_engine = self
        ctx._executed_steps = executed_steps
        ctx._results = results
        ctx._current_error_policy = self.error_policy

        if loop_def._collected_steps is not None:
            return list(loop_def._collected_steps)

        collected_steps: list[Step] = []
        _set_current_steps(collected_steps)
        try:
            loop_def.body(ctx)
        finally:
            _set_current_steps(None)
        loop_def._collected_steps = list(collected_steps)
        return collected_steps

    def _save_observability_data(self, loop_name: str) -> None:
        if self._collector:
            self._collector.end_loop(loop_name)
            if self.checkpoint_dir:
                try:
                    self._collector.save(Path(self.checkpoint_dir) / "metrics.json")
                except Exception as exc:
                    logger.warning("Failed to save metrics: %s", exc)

        if self._cost_tracker and self.checkpoint_dir:
            try:
                self._cost_tracker.save(Path(self.checkpoint_dir) / "costs.json")
            except Exception as exc:
                logger.warning("Failed to save cost data: %s", exc)

    def _emit_step_completed_event(
        self,
        job_id: str,
        step: Step,
        result: StepResult,
        step_idx: int,
        total_steps: int,
        totals: tuple[float, int],
    ) -> None:
        if not self.event_emitter:
            return
        total_cost, total_tokens = totals
        progress = min(1.0, round((step_idx + 1) / max(1, total_steps), 2))
        output_payload = (
            result.output.updates if isinstance(result.output, StepOutput) else result.output
        )
        self.event_emitter.emit(
            job_id=job_id,
            event_type="step_completed" if result.success else "step_failed",
            step_index=step_idx,
            metrics={
                "tokens_used": result.tokens_used,
                "cost": result.cost,
                "duration_ms": result.duration_ms,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
            },
            payload={
                "step_name": step.name,
                "success": result.success,
                "output": output_payload,
                "error": result.error,
                "progress": progress,
            },
        )

    def _emit_loop_summary_event(
        self,
        job_id: str,
        loop_name: str,
        executed_steps: list[str],
        results: dict[str, StepResult],
        summary: tuple[bool, str | None, float, int],
    ) -> None:
        if not self.event_emitter:
            return
        all_succeeded, first_error, total_cost, total_tokens = summary
        metrics = {"total_cost": total_cost, "total_tokens": total_tokens}
        if all_succeeded:
            output_results = {
                k: (v.output.updates if isinstance(v.output, StepOutput) else v.output)
                for k, v in results.items()
            }
            self.event_emitter.emit(
                job_id=job_id,
                event_type="loop_completed",
                step_index=len(executed_steps),
                metrics=metrics,
                payload={
                    "loop_name": loop_name,
                    "steps_executed": executed_steps,
                    "results": output_results,
                },
            )
        else:
            self.event_emitter.emit(
                job_id=job_id,
                event_type="loop_failed",
                step_index=len(executed_steps),
                metrics=metrics,
                payload={"loop_name": loop_name, "error": first_error},
            )

    def _execute_single_step(
        self,
        loop_def: Any,
        step: Step,
        ctx: Context,
        state: tuple[list[str], dict[str, StepResult]],
        job_meta: tuple[str, int],
    ) -> tuple[float, int]:
        executed_steps, results = state
        job_id, total_steps = job_meta
        step_idx = len(executed_steps)
        if self.event_emitter:
            self.event_emitter.emit(
                job_id=job_id,
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
            context=ctx,
            loop_def=loop_def,
            error_policy=self.error_policy,
            collector=self._collector,
            llm_client=self._llm_client,
            cost_tracker=self._cost_tracker,
            event_emitter=self.event_emitter,
            job_id=job_id,
            step_index=step_idx,
            total_steps=total_steps,
        )

        return apply_step_result(
            loop_def=loop_def,
            step=step,
            result=result,
            ctx=ctx,
            executed_steps=executed_steps,
            results=results,
            heartbeat=self._heartbeat,
            collector=self._collector,
            cost_tracker=self._cost_tracker,
            on_step_complete=self._on_step_complete,
        )

    def _run_steps_loop(
        self,
        loop_def: Any,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
        job_id: str,
    ) -> tuple[float, int]:
        collected_steps = self._collect_steps_from_loop(loop_def, ctx, executed_steps, results)
        total_steps = len(collected_steps)
        total_cost: float = 0.0
        total_tokens: int = 0
        ip = self.interruption_protection

        if self.event_emitter:
            self.event_emitter.emit(
                job_id=job_id,
                event_type="loop_started",
                step_index=0,
                payload={
                    "loop_name": loop_def.name,
                    "version": loop_def.version,
                    "total_steps": total_steps,
                    "budget": self.budget.to_dict() if self.budget else None,
                },
            )

        for step in collected_steps:
            if step.name in executed_steps:
                continue

            check_budget_limits(
                self.budget,
                loop_def,
                ctx,
                executed_steps,
                results,
                total_cost,
                total_tokens,
                self._heartbeat,
                self.checkpoint_dir,
            )
            if ip and ip.enabled and ip.pre_step_checkpoint:
                self._last_checkpoint = make_checkpoint(
                    loop_def, ctx, executed_steps, results, self.checkpoint_dir
                )

            step_idx = len(executed_steps)
            cost_added, tokens_added = self._execute_single_step(
                loop_def, step, ctx, (executed_steps, results), (job_id, total_steps)
            )
            total_cost += cost_added
            total_tokens += tokens_added

            self._emit_step_completed_event(
                job_id, step, results[step.name], step_idx, total_steps, (total_cost, total_tokens)
            )

            if (ip and ip.enabled and ip.post_step_checkpoint) or (not ip or not ip.enabled):
                self._last_checkpoint = make_checkpoint(
                    loop_def, ctx, executed_steps, results, self.checkpoint_dir
                )

        return total_cost, total_tokens

    def run(
        self,
        loop_def: Any,
        initial_context: dict[str, Any] | None = None,
        resume_checkpoint: CheckpointData | None = None,
        job_id: str | None = None,
    ) -> LoopRunResult:
        ctx, executed_steps, results, resume_cnt = init_run_state(
            initial_context, resume_checkpoint
        )
        self._resume_count = resume_cnt
        total_cost, total_tokens, interrupted = 0.0, 0, False
        ip = self.interruption_protection
        effective_job_id = job_id or f"{loop_def.name}_{int(time.time())}"

        if ip and ip.enabled:
            self._heartbeat = HeartbeatState()
            start_heartbeat(
                self._heartbeat, ip.heartbeat_timeout, ip.heartbeat_interval, loop_def.name
            )
        if self._collector:
            self._collector.start_loop(loop_def.name)

        try:
            if resume_checkpoint is None:
                loop_def._collected_steps = None
            total_cost, total_tokens = self._run_steps_loop(
                loop_def, ctx, executed_steps, results, effective_job_id
            )
        except (BudgetExceededError, InterruptedError, LoopError) as exc:
            if self.event_emitter:
                ev_type = (
                    "loop_interrupted"
                    if isinstance(exc, (BudgetExceededError, InterruptedError))
                    else "loop_failed"
                )
                self.event_emitter.emit(
                    job_id=effective_job_id,
                    event_type=ev_type,
                    step_index=len(executed_steps),
                    payload={"loop_name": loop_def.name, "error": str(exc)},
                )
            raise
        except Exception as exc:
            interrupted = True
            if self.event_emitter:
                self.event_emitter.emit(
                    job_id=effective_job_id,
                    event_type="loop_interrupted",
                    step_index=len(executed_steps),
                    payload={"loop_name": loop_def.name, "error": str(exc)},
                )
            self._last_checkpoint = make_checkpoint(
                loop_def, ctx, executed_steps, results, self.checkpoint_dir
            )
            raise InterruptedError(f"Loop '{loop_def.name}' interrupted: {exc}") from exc
        finally:
            if self._heartbeat:
                stop_heartbeat(self._heartbeat)
                self._heartbeat = None
            self._save_observability_data(loop_def.name)

        all_succeeded = all(r.success for r in results.values())
        first_error = next((r.error for r in results.values() if not r.success and r.error), None)
        self._emit_loop_summary_event(
            effective_job_id,
            loop_def.name,
            executed_steps,
            results,
            (all_succeeded, first_error, total_cost, total_tokens),
        )

        return LoopRunResult(
            success=all_succeeded,
            results=results,
            total_cost=total_cost,
            total_tokens=total_tokens,
            steps_executed=executed_steps,
            error=first_error,
            interrupted=interrupted,
            resume_count=self._resume_count,
            last_checkpoint=self._last_checkpoint,
            checkpoint_saved=self._last_checkpoint is not None,
        )
