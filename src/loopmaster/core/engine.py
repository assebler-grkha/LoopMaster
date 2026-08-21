"""LoopEngine — runtime that executes loop definitions.

The engine:
1. Receives a @Loop-decorated function (LoopDef)
2. Executes its body, intercepting Step() calls
3. Runs steps with error recovery, cost tracking, checkpoints
4. Supports resume from checkpoint via executed_step_names
5. Interruption protection via heartbeats and pre/post checkpoints
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .context import Context
from .exceptions import BudgetExceededError, InterruptedError
from .types import (
    Budget,
    CheckpointData,
    ErrorPolicy,
    InterruptionProtection,
    RecoveryAction,
    Step,
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
    error: Exception | None = None
    checkpoint_saved: bool = False
    interrupted: bool = False
    resume_count: int = 0
    last_checkpoint: CheckpointData | None = None


@dataclass
class _HeartbeatState:
    """Tracks heartbeat for interruption detection."""

    last_heartbeat: float = 0.0
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


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
        self._heartbeat: _HeartbeatState | None = None
        self._last_checkpoint: CheckpointData | None = None
        self._resume_count: int = 0

    def on_step_complete(
        self, callback: Callable[[StepResult], None]
    ) -> None:
        self._on_step_complete = callback

    def register(self, loop_def: Any) -> None:
        self._registry[loop_def.name] = loop_def

    def _start_heartbeat(self, loop_name: str) -> None:
        """Start heartbeat thread for interruption detection."""
        ip = self.interruption_protection
        if not ip or not ip.enabled:
            return

        self._heartbeat = _HeartbeatState()
        self._heartbeat.last_heartbeat = time.monotonic()
        hb = self._heartbeat

        def _heartbeat_loop() -> None:
            while not hb.stop_event.is_set():
                elapsed = (
                    time.monotonic() - hb.last_heartbeat
                )
                if elapsed > ip.heartbeat_timeout:
                    logger.warning(
                        "Heartbeat timeout after %.1fs — "
                        "interruption detected for loop %s",
                        elapsed,
                        loop_name,
                    )
                    hb.stop_event.set()
                    return
                hb.stop_event.wait(
                    timeout=ip.heartbeat_interval
                )

        self._heartbeat.thread = threading.Thread(
            target=_heartbeat_loop, daemon=True, name=f"heartbeat-{loop_name}"
        )
        self._heartbeat.thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop heartbeat thread."""
        if self._heartbeat:
            self._heartbeat.stop_event.set()
            if self._heartbeat.thread:
                self._heartbeat.thread.join(timeout=2.0)
            self._heartbeat = None

    def _ping_heartbeat(self) -> None:
        """Update heartbeat timestamp (called after each successful step)."""
        if self._heartbeat:
            self._heartbeat.last_heartbeat = time.monotonic()

    def _is_interrupted(self) -> bool:
        """Check if heartbeat detected interruption."""
        if self._heartbeat:
            return self._heartbeat.stop_event.is_set()
        return False

    def _make_checkpoint(
        self,
        loop_def: Any,
        ctx: Context,
        executed_steps: list[str],
        results: dict[str, StepResult],
    ) -> CheckpointData:
        """Create and persist a checkpoint."""
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
                    "output": v.output.updates if v.output else None,
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
            self._resume_count += 1

        results: dict[str, StepResult] = {}
        total_cost = 0.0
        total_tokens = 0
        interrupted = False

        if resume_checkpoint:
            for step_name, sr in resume_checkpoint.completed_results.items():
                results[step_name] = StepResult(**sr)

        ip = self.interruption_protection
        if ip and ip.enabled:
            self._start_heartbeat(loop_def.name)

        try:
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

                if self._is_interrupted():
                    interrupted = True
                    self._make_checkpoint(
                        loop_def, ctx, executed_steps, results
                    )
                    raise InterruptedError(
                        f"Loop '{loop_def.name}' interrupted — "
                        "heartbeat timeout"
                    )

                if (
                    self.budget
                    and self.budget.max_steps is not None
                    and len(executed_steps) >= self.budget.max_steps
                ):
                    self._make_checkpoint(
                        loop_def, ctx, executed_steps, results
                    )
                    raise BudgetExceededError(
                        budget_limit=float(self.budget.max_steps),
                        spent=float(len(executed_steps)),
                    )

                if (
                    self.budget
                    and self.budget.max_cost is not None
                    and total_cost >= self.budget.max_cost
                ):
                    self._make_checkpoint(
                        loop_def, ctx, executed_steps, results
                    )
                    raise BudgetExceededError(
                        budget_limit=self.budget.max_cost,
                        spent=total_cost,
                    )

                if ip and ip.enabled and ip.pre_step_checkpoint:
                    self._make_checkpoint(
                        loop_def, ctx, executed_steps, results
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

                self._ping_heartbeat()

                if self._on_step_complete:
                    self._on_step_complete(result)

                if ip and ip.enabled and ip.post_step_checkpoint or not ip or not ip.enabled:
                    self._make_checkpoint(
                        loop_def, ctx, executed_steps, results
                    )

        except (BudgetExceededError, InterruptedError):
            raise
        except Exception as exc:
            interrupted = True
            self._make_checkpoint(
                loop_def, ctx, executed_steps, results
            )
            raise InterruptedError(
                f"Loop '{loop_def.name}' interrupted: {exc}"
            ) from exc
        finally:
            self._stop_heartbeat()

        all_succeeded = all(r.success for r in results.values())

        return LoopRunResult(
            success=all_succeeded,
            results=results,
            total_cost=total_cost,
            total_tokens=total_tokens,
            steps_executed=executed_steps,
            interrupted=interrupted,
            resume_count=self._resume_count,
            last_checkpoint=self._last_checkpoint,
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
                    backoff = error_policy.backoff * (2**attempt)
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(time.sleep, backoff)
                    except RuntimeError:
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
