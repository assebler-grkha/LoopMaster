"""Step execution logic with error recovery and LLM integration."""

from __future__ import annotations

import time
from typing import Any

from .context import Context
from .types import RecoveryAction, Step, StepResult, resolve_prompt


def _run_step_once(
    step: Step,
    context: Context,
    llm_client: Any = None,
    cost_tracker: Any = None,
) -> StepResult:
    """Execute a single attempt of a step."""
    ctx_data = context.to_dict()

    if step._engine_callback:
        return step.execute(ctx_data)

    # If this step has a prompt/model and an LLM client is available, execute via LLM
    if llm_client and (step.prompt or step.model):
        start = time.monotonic()
        try:
            resolved_prompt = resolve_prompt(step.prompt or "", ctx_data)
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
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            return StepResult(
                step_name=step.name,
                success=False,
                error=str(exc),
                duration_ms=duration,
                model=step.model,
            )

    return step.execute(ctx_data)


def execute_step(
    step: Step,
    context: Context,
    loop_def: Any,
    error_policy: Any,
    collector: Any = None,
    llm_client: Any = None,
    cost_tracker: Any = None,
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

    Returns:
        StepResult with success/failure status.
    """
    ctx_policy = step.on_error or getattr(context, "_current_error_policy", error_policy)
    step_retries = step.retry if step.retry is not None else (ctx_policy.retry if ctx_policy else 1)
    max_retries = step_retries if step_retries > 0 else 1

    for attempt in range(max_retries):
        result = _run_step_once(step, context, llm_client, cost_tracker)

        if result.success:
            return result

        recovery = ctx_policy.classify(result.error or "") if ctx_policy else RecoveryAction.ABORT

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
            fallback_result = _run_step_once(fallback_step, context, llm_client, cost_tracker)
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
        model=step.model,
    )
