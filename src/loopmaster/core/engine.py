"""LoopEngine — runtime that executes loop definitions and conditional branches.

The engine:
1. Receives a @Loop-decorated function (LoopDef)
2. Executes its body, intercepting Step() and Conditional() calls
3. Runs steps with error recovery, cost tracking, checkpoints
4. Supports resume from checkpoint via executed_step_names and branch stickiness
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
from .exceptions import BudgetExceededError, InterruptedError
from .heartbeat import (
    HeartbeatState,
    start_heartbeat,
    stop_heartbeat,
)
from .runner import execute_traced_loop, run_step_block
from .state import (
    emit_loop_summary_event,
    init_run_state,
    save_observability_data,
)
from .types import (
    Budget,
    CheckpointData,
    ErrorPolicy,
    InterruptionProtection,
    StepResult,
)

logger = logging.getLogger(__name__)

_local = threading.local()


def _get_current_steps() -> list[Any] | None:
    return getattr(_local, "steps", None)


def _set_current_steps(steps: list[Any] | None) -> None:
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
        compatibility_policy: Any = None,
        model_registry: Any = None,
        model_policy: Any = None,
        cancel_event: Any = None,
    ) -> None:
        self.error_policy = error_policy or ErrorPolicy()
        self.budget = budget
        self.interruption_protection = interruption_protection
        self.checkpoint_dir = checkpoint_dir
        self._collector = metrics_collector
        self._cost_tracker = cost_tracker
        self._llm_client = llm_client
        self.event_emitter = event_emitter
        self.compatibility_policy = compatibility_policy
        self.model_registry = model_registry
        self.model_policy = model_policy
        self._registry: dict[str, Any] = {}
        self._on_step_complete: Callable[[StepResult], None] | None = None
        self._heartbeat: HeartbeatState | None = None
        self._last_checkpoint: CheckpointData | None = None
        self._resume_count: int = 0
        self._cancel_event = cancel_event

    def on_step_complete(self, callback: Callable[[StepResult], None]) -> None:
        self._on_step_complete = callback

    def register(self, loop_def: Any) -> None:
        self._registry[loop_def.name] = loop_def

    def _collect_steps_from_loop(
        self,
        loop_def: Any,
        ctx: Context | None,
        executed_steps: list[str],
        results: dict[str, StepResult],
    ) -> list[Any]:
        effective_ctx = ctx if ctx is not None else Context({})
        effective_ctx._loop_engine = self
        effective_ctx._executed_steps = executed_steps
        effective_ctx._results = results
        effective_ctx._current_error_policy = self.error_policy

        if loop_def._collected_steps is not None:
            return list(loop_def._collected_steps)

        collected: list[Any] = []
        _set_current_steps(collected)
        try:
            loop_def.body(effective_ctx)
        finally:
            _set_current_steps(None)
        loop_def._collected_steps = list(collected)
        return collected

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
        totals: list[Any] = [0.0, 0]

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

        from .runner import BlockExecContext

        bctx = BlockExecContext(
            engine=self,
            loop_def=loop_def,
            ctx=ctx,
            executed_steps=executed_steps,
            results=results,
            job_id=job_id,
            total_steps=total_steps,
            totals=totals,
        )

        for block in collected_steps:
            run_step_block(bctx, block)

        return totals[0], totals[1]

    def _handle_loop_error(
        self, job_id: str, loop_name: str, step_count: int, exc: Exception
    ) -> None:
        if self.event_emitter:
            ev_type = (
                "loop_interrupted"
                if isinstance(exc, (BudgetExceededError, InterruptedError))
                else "loop_failed"
            )
            self.event_emitter.emit(
                job_id=job_id,
                event_type=ev_type,
                step_index=step_count,
                payload={"loop_name": loop_name, "error": str(exc)},
            )

    def run(
        self,
        loop_def: Any,
        initial_context: dict[str, Any] | None = None,
        resume_checkpoint: CheckpointData | None = None,
        job_id: str | None = None,
    ) -> LoopRunResult:
        ctx, executed_steps, results, resume_cnt = init_run_state(
            initial_context=initial_context,
            resume_checkpoint=resume_checkpoint,
            loop_def=loop_def,
            compatibility_policy=self.compatibility_policy,
        )
        self._resume_count = resume_cnt
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
            total_cost, total_tokens, interrupted = execute_traced_loop(
                self,
                loop_def,
                ctx,
                (executed_steps, results),
                (effective_job_id, resume_cnt, resume_checkpoint),
            )
        finally:
            if self._heartbeat:
                stop_heartbeat(self._heartbeat)
                self._heartbeat = None
            save_observability_data(
                loop_def.name, self._collector, self._cost_tracker, self.checkpoint_dir
            )

        all_succeeded = all(r.success for r in results.values())
        first_error = next((r.error for r in results.values() if not r.success and r.error), None)
        emit_loop_summary_event(
            self.event_emitter,
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
