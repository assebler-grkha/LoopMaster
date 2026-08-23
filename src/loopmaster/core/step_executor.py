"""Step execution logic with error recovery, LLM integration, and real-time streaming."""

from __future__ import annotations

import time
from typing import Any

from .context import Context
from .types import RecoveryAction, Step, StepResult, resolve_prompt


def _run_streaming_step(
    step: Step,
    resolved_prompt: str,
    llm_client: Any,
    cost_tracker: Any,
    event_emitter: Any,
    job_id: str,
    step_index: int,
) -> StepResult:
    """Execute step via streaming LLM API."""
    start = time.monotonic()
    chunks: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    model_used = step.model or "gpt-4o"

    for chunk in llm_client.stream_complete(prompt=resolved_prompt, model=step.model):
        if chunk.delta:
            chunks.append(chunk.delta)
            event_emitter.emit(
                job_id=job_id,
                event_type="step_chunk",
                step_index=step_index,
                payload={
                    "step_name": step.name,
                    "delta": chunk.delta,
                    "accumulated": "".join(chunks),
                },
            )
        if chunk.is_final or chunk.prompt_tokens > 0 or chunk.completion_tokens > 0:
            prompt_tokens = chunk.prompt_tokens or prompt_tokens
            completion_tokens = chunk.completion_tokens or completion_tokens
            total_tokens = chunk.total_tokens or (prompt_tokens + completion_tokens)
            if chunk.model:
                model_used = chunk.model

    content = "".join(chunks)
    duration = (time.monotonic() - start) * 1000
    cost = 0.0
    if cost_tracker:
        cost = cost_tracker.calculate_cost(
            model=model_used,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

    return StepResult(
        step_name=step.name,
        success=True,
        output=content,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        tokens_used=total_tokens or (prompt_tokens + completion_tokens),
        cost=cost,
        duration_ms=duration,
        model=model_used,
    )


def _run_sync_step(
    step: Step,
    resolved_prompt: str,
    llm_client: Any,
    cost_tracker: Any,
) -> StepResult:
    """Execute step via synchronous non-streaming complete() call."""
    start = time.monotonic()
    resp = llm_client.complete(prompt=resolved_prompt, model=step.model)
    duration = (time.monotonic() - start) * 1000

    model_used = resp.model or step.model or "gpt-4o"
    cost = 0.0
    if cost_tracker:
        cost = cost_tracker.calculate_cost(
            model=model_used,
            input_tokens=resp.prompt_tokens,
            output_tokens=resp.completion_tokens,
        )

    return StepResult(
        step_name=step.name,
        success=True,
        output=resp.content,
        input_tokens=resp.prompt_tokens,
        output_tokens=resp.completion_tokens,
        tokens_used=resp.total_tokens,
        cost=cost,
        duration_ms=duration,
        model=model_used,
    )


def _run_step_once(
    step: Step,
    context: Context,
    llm_client: Any = None,
    cost_tracker: Any = None,
    event_emitter: Any = None,
    job_id: str = "",
    step_index: int = 0,
    total_steps: int = 0,
) -> StepResult:
    """Execute a single attempt of a step."""
    ctx_data = context.to_dict()

    if step._engine_callback:
        return step.execute(ctx_data)

    if not llm_client or (not step.prompt and not step.model):
        return step.execute(ctx_data)

    start = time.monotonic()
    try:
        resolved_prompt = resolve_prompt(step.prompt or "", ctx_data)

        if event_emitter and hasattr(llm_client, "stream_complete"):
            return _run_streaming_step(
                step=step,
                resolved_prompt=resolved_prompt,
                llm_client=llm_client,
                cost_tracker=cost_tracker,
                event_emitter=event_emitter,
                job_id=job_id,
                step_index=step_index,
            )

        return _run_sync_step(
            step=step,
            resolved_prompt=resolved_prompt,
            llm_client=llm_client,
            cost_tracker=cost_tracker,
        )
    except Exception as exc:
        duration = (time.monotonic() - start) * 1000
        return StepResult(
            step_name=step.name,
            success=False,
            error=str(exc),
            duration_ms=duration,
            model=step.model,
        )


def execute_step(
    step: Step,
    context: Context,
    loop_def: Any,
    error_policy: Any,
    collector: Any = None,
    llm_client: Any = None,
    cost_tracker: Any = None,
    event_emitter: Any = None,
    job_id: str = "",
    step_index: int = 0,
    total_steps: int = 0,
) -> StepResult:
    """Execute a step with retry/backoff/skip/fallback logic.

    Args:
        step: The Step to execute.
        context: Current loop context.
        loop_def: The loop definition being executed.
        error_policy: ErrorPolicy for recovery decisions.
        collector: Optional MetricsCollector for retry recording.
        llm_client: Optional LLMClient for executing prompt/model steps.
        cost_tracker: Optional CostTracker for calculating step cost.
        event_emitter: Optional EventEmitter for real-time lifecycle and chunk events.
        job_id: Unique loop execution ID.
        step_index: Current step index (0-based).
        total_steps: Total steps count.

    Returns:
        StepResult with success/failure status.
    """
    ctx_policy = step.on_error or getattr(context, "_current_error_policy", error_policy)
    step_retries = step.retry if step.retry is not None else (ctx_policy.retry if ctx_policy else 1)
    max_retries = step_retries if step_retries > 0 else 1

    for attempt in range(max_retries):
        result = _run_step_once(
            step=step,
            context=context,
            llm_client=llm_client,
            cost_tracker=cost_tracker,
            event_emitter=event_emitter,
            job_id=job_id,
            step_index=step_index,
            total_steps=total_steps,
        )

        if result.success:
            return result

        recovery = ctx_policy.classify(result.error or "") if ctx_policy else RecoveryAction.ABORT

        if recovery == RecoveryAction.RETRY and attempt < max_retries - 1:
            if collector:
                collector.record_retry(loop_def.name, step.name)
            backoff = ctx_policy.backoff * (2**attempt)
            if event_emitter:
                event_emitter.emit(
                    job_id=job_id,
                    event_type="step_retry",
                    step_index=step_index,
                    payload={
                        "step_name": step.name,
                        "attempt": attempt + 2,
                        "error": result.error,
                        "backoff_seconds": backoff,
                        "reset_buffer": True,
                    },
                )
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
            fallback_result = _run_step_once(
                step=fallback_step,
                context=context,
                llm_client=llm_client,
                cost_tracker=cost_tracker,
                event_emitter=event_emitter,
                job_id=job_id,
                step_index=step_index,
                total_steps=total_steps,
            )
            return fallback_result
        elif attempt < max_retries - 1:
            backoff = ctx_policy.backoff * (2**attempt)
            if event_emitter:
                event_emitter.emit(
                    job_id=job_id,
                    event_type="step_retry",
                    step_index=step_index,
                    payload={
                        "step_name": step.name,
                        "attempt": attempt + 2,
                        "error": result.error,
                        "backoff_seconds": backoff,
                        "reset_buffer": True,
                    },
                )
            time.sleep(backoff)
            continue
        else:
            return result

    return StepResult(
        step_name=step.name,
        success=False,
        error="Max retries exceeded",
        model=step.model,
    )
