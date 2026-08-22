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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..cost.tracker import CostTracker
from ..metrics.collector import MetricsCollector
from .context import Context
from .exceptions import BudgetExceededError, InterruptedError, LoopError
from .heartbeat import (
    HeartbeatState,
    is_interrupted,
    ping_heartbeat,
    start_heartbeat,
    stop_heartbeat,
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
    checkpoint_saved: bool = False
    interrupted: bool = False
    resume_count: int = 0
    last_checkpoint: CheckpointData | None = None


class LoopEngine:
    """Runtime engine for executing loop definitions."""

    def __init__(
        self,
        error_policy: ErrorPolicy | None = None,
        budget: Budget | None = None,
        interruption_protection: InterruptionProtection | None = None,
        checkpoint_dir: str | None = None,
        metrics_collector: MetricsCollector | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.error_policy = error_policy or ErrorPolicy()
        self.budget = budget
        self.interruption_protection = interruption_protection
        self.checkpoint_dir = checkpoint_dir
        self._collector = metrics_collector
        self._cost_tracker = cost_tracker
        self._registry: dict[str, Any] = {}
        self._on_step_complete: Callable[[StepResult], None] | None = None
        self._heartbeat: HeartbeatState | None = None
        self._last_checkpoint: CheckpointData | None = None
        self._resume_count: int = 0

    def on_step_complete(self, callback: Callable[[StepResult], None]) -> None:
        self._on_step_complete = callback

    def register(self, loop_def: Any) -> None:
        self._registry[loop_def.name] = loop_def

    # -- checkpoint ----------------------------------------------------------

    def _make_checkpoint(
        self,
        loop_def: Any,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
    ) -> CheckpointData:
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
        self._last_checkpoint = cp

        if self.checkpoint_dir:
            try:
                from ..checkpoint import CheckpointManager

                mgr = CheckpointManager(self.checkpoint_dir)
                mgr.save(cp)
            except Exception as exc:
                logger.warning("Failed to save checkpoint: %s", exc)

        return cp

    # -- run sub-steps -------------------------------------------------------

    def _init_run_state(
        self,
        initial_context: dict[str, Any] | None,
        resume_checkpoint: CheckpointData | None,
    ) -> tuple[Context, list[str], dict[str, StepResult]]:
        ctx = Context(initial_context or {})
        executed_steps: list[str] = []
        results: dict[str, StepResult] = {}

        if resume_checkpoint:
            ctx = Context(resume_checkpoint.context_data)
            executed_steps = list(resume_checkpoint.executed_step_names)
            self._resume_count += 1
            for step_name, sr in resume_checkpoint.completed_results.items():
                results[step_name] = StepResult(**sr)

        return ctx, executed_steps, results

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

    def _check_budget_limits(
        self,
        loop_def: Any,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
        total_cost: float,
    ) -> None:
        if self._heartbeat and is_interrupted(self._heartbeat):
            self._make_checkpoint(loop_def, ctx, executed_steps, results)
            raise InterruptedError(f"Loop '{loop_def.name}' interrupted — heartbeat timeout")

        if (
            self.budget
            and self.budget.max_steps is not None
            and len(executed_steps) >= self.budget.max_steps
        ):
            self._make_checkpoint(loop_def, ctx, executed_steps, results)
            raise BudgetExceededError(
                budget_limit=float(self.budget.max_steps),
                spent=float(len(executed_steps)),
                unit="",
            )

        if self.budget and self.budget.max_cost is not None and total_cost >= self.budget.max_cost:
            self._make_checkpoint(loop_def, ctx, executed_steps, results)
            raise BudgetExceededError(
                budget_limit=self.budget.max_cost,
                spent=total_cost,
            )

    def _apply_step_result(
        self,
        loop_def: Any,
        step: Step,
        result: StepResult,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
    ) -> tuple[float, int]:
        executed_steps.append(step.name)
        results[step.name] = result
        ctx._executed_steps = executed_steps
        ctx._results = results

        if result.success and result.output is not None:
            if isinstance(result.output, dict):
                ctx.merge(result.output)
            else:
                output_updates = getattr(result.output, "updates", None)
                if output_updates:
                    ctx.merge(output_updates)

        cost_added = result.cost or 0.0
        tokens_added = result.tokens_used or 0

        if self._heartbeat:
            ping_heartbeat(self._heartbeat)

        if self._collector:
            self._collector.record_step(
                loop_name=loop_def.name,
                step_name=step.name,
                cost=result.cost,
                tokens=result.tokens_used,
                duration_ms=result.duration_ms,
                success=result.success,
            )

        if self._cost_tracker and step.model:
            input_tokens = result.tokens_used if result.tokens_used else 0
            self._cost_tracker.record(
                model=step.model,
                input_tokens=input_tokens,
                output_tokens=0,
                step_name=step.name,
            )

        if self._on_step_complete:
            self._on_step_complete(result)

        return cost_added, tokens_added

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

    # -- main entry point ----------------------------------------------------

    def run(
        self,
        loop_def: Any,
        initial_context: dict[str, Any] | None = None,
        resume_checkpoint: CheckpointData | None = None,
    ) -> LoopRunResult:
        self._resume_count = 0
        ctx, executed_steps, results = self._init_run_state(initial_context, resume_checkpoint)
        total_cost, total_tokens, interrupted = 0.0, 0, False
        ip = self.interruption_protection

        if ip and ip.enabled:
            self._heartbeat = HeartbeatState()
            start_heartbeat(
                self._heartbeat,
                ip.heartbeat_timeout,
                ip.heartbeat_interval,
                loop_def.name,
            )
        if self._collector:
            self._collector.start_loop(loop_def.name)

        try:
            if resume_checkpoint is None:
                loop_def._collected_steps = None
            collected_steps = self._collect_steps_from_loop(loop_def, ctx, executed_steps, results)

            for step in collected_steps:
                if step.name in executed_steps:
                    continue

                self._check_budget_limits(loop_def, ctx, executed_steps, results, total_cost)

                if ip and ip.enabled and ip.pre_step_checkpoint:
                    self._make_checkpoint(loop_def, ctx, executed_steps, results)

                result = execute_step(
                    step=step,
                    context=ctx,
                    loop_def=loop_def,
                    error_policy=self.error_policy,
                    collector=self._collector,
                )

                cost_added, tokens_added = self._apply_step_result(
                    loop_def, step, result, ctx, executed_steps, results
                )
                total_cost += cost_added
                total_tokens += tokens_added

                should_checkpoint = (ip and ip.enabled and ip.post_step_checkpoint) or (
                    not ip or not ip.enabled
                )
                if should_checkpoint:
                    self._make_checkpoint(loop_def, ctx, executed_steps, results)

        except (BudgetExceededError, InterruptedError):
            raise
        except LoopError:
            raise
        except Exception as exc:
            interrupted = True
            self._make_checkpoint(loop_def, ctx, executed_steps, results)
            raise InterruptedError(f"Loop '{loop_def.name}' interrupted: {exc}") from exc
        finally:
            if self._heartbeat:
                stop_heartbeat(self._heartbeat)
                self._heartbeat = None
            self._save_observability_data(loop_def.name)

        all_succeeded = all(r.success for r in results.values())

        first_error: str | None = None
        if not all_succeeded:
            for r in results.values():
                if not r.success and r.error is not None:
                    first_error = r.error
                    break

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
