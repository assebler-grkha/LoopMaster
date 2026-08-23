"""Step execution with retry logic, streaming, model policy validation, and GenAI spans."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from ..models import get_default_registry
from .context import Context
from .exceptions import ModelPolicyError, UnapprovedModelError
from .policies import RecoveryAction
from .types import Step, StepResult, resolve_prompt

logger = logging.getLogger("loopmaster.core.step_executor")


@dataclass
class StepExecParams:
    """Execution parameters for step runs."""

    step: Step
    context: Context
    loop_def: Any
    error_policy: Any
    collector: Any = None
    llm_client: Any = None
    cost_tracker: Any = None
    event_emitter: Any = None
    job_id: str = ""
    step_index: int = 0
    total_steps: int = 0


def _run_streaming_step(
    step: Step,
    resolved_prompt: str,
    llm_client: Any,
    cost_tracker: Any,
    event_context: tuple[Any, str, int],
) -> StepResult:
    """Execute a step with token streaming and real-time SSE emission."""
    emitter, job_id, step_index = event_context
    start = time.monotonic()
    chunks: list[str] = []
    total_in, total_out = 0, 0
    resp_model = step.model or "default"

    try:
        stream_iter = llm_client.stream_complete(resolved_prompt, model=step.model)
    except TypeError:
        stream_iter = llm_client.stream_complete(resolved_prompt)

    for chunk in stream_iter:
        delta_text = getattr(chunk, "delta", getattr(chunk, "text", ""))
        if delta_text:
            chunks.append(delta_text)
            if emitter:
                emitter.emit(
                    job_id=job_id,
                    event_type="step_chunk",
                    step_index=step_index,
                    payload={
                        "step_name": step.name,
                        "delta": delta_text,
                        "model": getattr(chunk, "model", "") or step.model,
                    },
                )
        if getattr(chunk, "prompt_tokens", 0):
            total_in = chunk.prompt_tokens
        if getattr(chunk, "completion_tokens", 0):
            total_out = chunk.completion_tokens
        if getattr(chunk, "usage", None):
            total_in = chunk.usage.get("prompt_tokens", total_in)
            total_out = chunk.usage.get("completion_tokens", total_out)
        if getattr(chunk, "model", ""):
            resp_model = chunk.model

    full_text = "".join(chunks)
    duration = (time.monotonic() - start) * 1000
    if total_in == 0:
        total_in = max(1, len(resolved_prompt) // 4)
    if total_out == 0:
        total_out = max(1, len(full_text) // 4)

    cost = 0.0
    if cost_tracker:
        cost = cost_tracker.calculate_cost(
            model=resp_model, input_tokens=total_in, output_tokens=total_out
        )

    return StepResult(
        step_name=step.name,
        success=True,
        output=full_text,
        input_tokens=total_in,
        output_tokens=total_out,
        tokens_used=total_in + total_out,
        cost=cost,
        duration_ms=duration,
        model=resp_model,
    )


def _run_sync_step(
    step: Step,
    resolved_prompt: str,
    llm_client: Any,
    cost_tracker: Any,
) -> StepResult:
    """Execute a synchronous LLM call for a step."""
    start = time.monotonic()
    try:
        resp = llm_client.complete(resolved_prompt, model=step.model)
    except TypeError:
        resp = llm_client.complete(resolved_prompt)

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


def _validate_and_resolve_step_model(step: Step, context: Context, prompt_text: str) -> str:
    """Resolve aliases (@fast, @smart) and validate against ModelPolicy."""
    engine = getattr(context, "_loop_engine", None)
    registry = getattr(engine, "model_registry", None) or get_default_registry()
    policy = getattr(engine, "model_policy", None)

    est_tokens = max(1, len(prompt_text) // 4)
    spec = registry.validate_execution(step.model, policy, estimated_prompt_tokens=est_tokens)
    return spec.name


def _run_step_once(params: StepExecParams) -> StepResult:
    """Execute a single attempt of a step."""
    step = params.step
    ctx_data = params.context.to_dict()

    if (
        step.executor
        or step._engine_callback
        or not params.llm_client
        or (not step.prompt and not step.model)
    ):
        return step.execute(ctx_data)

    start = time.monotonic()
    try:
        resolved_prompt = resolve_prompt(step.prompt or "", ctx_data)
        concrete_model = _validate_and_resolve_step_model(step, params.context, resolved_prompt)
        step_with_model = Step(
            name=step.name,
            model=concrete_model,
            prompt=step.prompt,
            input=step.input,
            executor=step.executor,
        )

        from ..telemetry import SpanKind, get_tracer

        tracer = get_tracer()
        provider = getattr(getattr(params.llm_client, "config", None), "provider", "custom")

        with tracer.start_as_current_span(
            f"llm.{concrete_model}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.request.model": concrete_model,
                "gen_ai.operation.name": "chat",
                "gen_ai.system": str(provider),
            },
        ) as llm_span:
            if params.event_emitter and hasattr(params.llm_client, "stream_complete"):
                res = _run_streaming_step(
                    step=step_with_model,
                    resolved_prompt=resolved_prompt,
                    llm_client=params.llm_client,
                    cost_tracker=params.cost_tracker,
                    event_context=(params.event_emitter, params.job_id, params.step_index),
                )
            else:
                res = _run_sync_step(
                    step=step_with_model,
                    resolved_prompt=resolved_prompt,
                    llm_client=params.llm_client,
                    cost_tracker=params.cost_tracker,
                )

            llm_span.set_attribute("gen_ai.response.model", res.model)
            llm_span.set_attribute("gen_ai.usage.input_tokens", res.input_tokens)
            llm_span.set_attribute("gen_ai.usage.output_tokens", res.output_tokens)
            llm_span.set_attribute("gen_ai.usage.cost", res.cost)
            return res
    except (UnapprovedModelError, ModelPolicyError):
        raise
    except Exception as exc:
        duration = (time.monotonic() - start) * 1000
        return StepResult(
            step_name=step.name,
            success=False,
            error=str(exc),
            duration_ms=duration,
            model=step.model,
        )


def _execute_fallback_step(params: StepExecParams, fallback_model: str) -> StepResult:
    """Execute a fallback step when all primary retries fail."""
    fallback_step = Step(
        name=params.step.name,
        model=fallback_model,
        prompt=params.step.prompt,
        input=params.step.input,
    )
    fallback_params = StepExecParams(
        step=fallback_step,
        context=params.context,
        loop_def=params.loop_def,
        error_policy=params.error_policy,
        collector=params.collector,
        llm_client=params.llm_client,
        cost_tracker=params.cost_tracker,
        event_emitter=params.event_emitter,
        job_id=params.job_id,
        step_index=params.step_index,
        total_steps=params.total_steps,
    )
    return _run_step_once(fallback_params)


def _execute_step_retries(params: StepExecParams) -> StepResult:
    """Execute retry loop for a step with fallback support."""
    step = params.step
    ctx_policy = step.on_error or getattr(
        params.context, "_current_error_policy", params.error_policy
    )
    step_retries = step.retry if step.retry is not None else (ctx_policy.retry if ctx_policy else 1)
    max_retries = step_retries if step_retries > 0 else 1

    last_result: StepResult | None = None
    for attempt in range(max_retries):
        result = _run_step_once(params)
        last_result = result
        if result.success:
            return result

        recovery = ctx_policy.classify(result.error or "") if ctx_policy else RecoveryAction.ABORT
        if recovery == RecoveryAction.SKIP:
            return result

        if (
            recovery == RecoveryAction.RETRY or attempt < max_retries - 1
        ) and attempt < max_retries - 1:
            if params.collector:
                params.collector.record_retry(params.loop_def.name, step.name)
            backoff = ctx_policy.backoff * (2**attempt)
            if params.event_emitter:
                params.event_emitter.emit(
                    job_id=params.job_id,
                    event_type="step_retry",
                    step_index=params.step_index,
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

    # After retries exhausted, check fallback
    if (
        ctx_policy
        and ctx_policy.fallback_model
        and (
            ctx_policy.on_failure == RecoveryAction.FALLBACK or recovery == RecoveryAction.FALLBACK
        )
    ):
        return _execute_fallback_step(params, ctx_policy.fallback_model)

    return last_result or StepResult(
        step_name=step.name, success=False, error="Max retries exceeded", model=step.model
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
    """Execute a step with error policies, retries, and telemetry."""
    params = StepExecParams(
        step=step,
        context=context,
        loop_def=loop_def,
        error_policy=error_policy,
        collector=collector,
        llm_client=llm_client,
        cost_tracker=cost_tracker,
        event_emitter=event_emitter,
        job_id=job_id,
        step_index=step_index,
        total_steps=total_steps,
    )
    from ..telemetry import SpanStatusCode, get_tracer

    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"step.{step.name}",
        attributes={"loopmaster.step.name": step.name, "loopmaster.step.model": str(step.model)},
    ) as span:
        res = _execute_step_retries(params)
        span.set_attribute("loopmaster.step.success", res.success)
        span.set_attribute("loopmaster.step.cost", res.cost)
        span.set_attribute("loopmaster.step.tokens_used", res.tokens_used)
        if not res.success:
            span.set_status(SpanStatusCode.ERROR, res.error or "Step execution failed")
        return res
