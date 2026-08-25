"""Loop execution MCP tools: detached spec runs and legacy Python-DSL runs."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import loopmaster.mcp.runtime as rt
from loopmaster.core.engine import LoopEngine
from loopmaster.core.policies import RecoveryAction
from loopmaster.core.types import ErrorPolicy
from loopmaster.core.types import LoopDef as LoopDefType
from loopmaster.cost.tracker import CostTracker
from loopmaster.llm import LLMClient, get_llm_config
from loopmaster.mcp.discovery import find_loop_files, load_loop_def, load_loop_def_object
from loopmaster.mcp.runtime import mcp
from loopmaster.mcp.tools_blocks import validate_code_refs
from loopmaster.mcp.tools_notifications import with_pending
from loopmaster.metrics.collector import MetricsCollector
from loopmaster.spec import SpecValidationError, load_loop_from_dict


def _run_spec_json(spec_json: str, context: str, mode: str) -> str:
    """Run a LoopSpec v1 JSON document through the detached worker."""
    if mode not in ("detached", "agent"):
        return json.dumps(
            {"error": f"mode '{mode}' is not supported for spec_json; use 'detached' or 'agent'."}
        )

    try:
        data = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    try:
        loop_def, spec = load_loop_from_dict(data)
    except SpecValidationError as exc:
        return json.dumps({"error": str(exc)})

    ref_error = validate_code_refs(data)
    if ref_error:
        return json.dumps({"error": ref_error})

    try:
        ctx_data = json.loads(context) if isinstance(context, str) else dict(context)
    except Exception as exc:
        return json.dumps({"error": f"Invalid context JSON: {exc}"})

    if mode == "agent":
        return _create_agent_job(data, spec)

    job_id = rt.runner.submit(
        loop_def,
        initial_context=ctx_data or dict(spec.initial_context),
        definition={"spec": data, "step_count": len(spec.step_names())},
    )
    return json.dumps(
        with_pending(
            {
                "job_id": job_id,
                "status": "running",
                "loop_name": spec.name,
                "execution_mode": mode,
                "steps": spec.step_names(),
                "message": (
                    "Detached loop started in worker thread. "
                    "Use loop_status/loop_result to poll; loop_cancel to stop."
                ),
            }
        ),
        indent=2,
    )


def _create_agent_job(data: dict[str, Any], spec: Any) -> str:
    """Create an agent-execution job: no worker thread, the calling agent runs steps."""
    job_id = f"{spec.name}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    rt.store.create_job(
        job_id=job_id,
        loop_name=spec.name,
        definition={"spec": data, "step_count": len(spec.step_names()), "execution": "agent"},
        status="ready",
        total_steps=len(spec.step_names()),
        metrics={"host_pid": os.getpid(), "execution": "agent"},
    )
    return json.dumps(
        with_pending(
            {
                "job_id": job_id,
                "status": "ready",
                "loop_name": spec.name,
                "execution_mode": "agent",
                "steps": spec.step_names(),
                "message": (
                    "Agent-execution job created. Execute each root step yourself (llm steps "
                    "= your own model) and record progress via loop_record(job_id, step_name); "
                    "the job auto-completes when every step is recorded."
                ),
            }
        ),
        indent=2,
    )


@mcp.tool()
def loop_record(
    job_id: str,
    step_name: str,
    success: bool = True,
    output: str | None = None,
    error: str | None = None,
) -> str:
    """Record the result of an agent-executed step (execution mode 'agent').

    Call once per root step from the plan returned by loop_run(mode='agent').
    The job transitions ready -> in_progress and auto-completes when all
    recorded steps cover total_steps. Failed steps keep the run in_progress
    with an error attached; finalize explicitly via this tool semantics.
    """
    job = rt.store.get_job(job_id)
    if not job:
        return json.dumps({"error": f"Job '{job_id}' not found."})

    parsed_output: Any = None
    if output is not None:
        try:
            parsed_output = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            parsed_output = output

    updated = rt.store.record_step_result(
        job_id=job_id,
        step_name=step_name,
        success=success,
        output=parsed_output,
        error=error,
    )
    if updated is None:
        return json.dumps({"error": f"Failed to record step for '{job_id}'."})
    return json.dumps(
        with_pending(
            {
                "recorded": True,
                "job_id": job_id,
                "step_name": step_name,
                "status": updated.status,
                "current_step": updated.current_step,
                "total_steps": updated.total_steps,
                "error": updated.error,
            }
        ),
        indent=2,
    )


def _find_target_loop_def(
    loop_name: str, search_dir: str | None
) -> tuple[Path | None, LoopDefType | None]:
    """Find the target loop definition object by name."""
    search = Path(search_dir) if search_dir else None
    for f in find_loop_files(search):
        ldef = load_loop_def_object(f)
        if ldef and ldef.name == loop_name:
            return f, ldef
    return None, None


def _format_completed_results(run_result: Any) -> dict[str, Any]:
    """Format step results into serializable dict (failures included)."""
    out: dict[str, Any] = {}
    for name, res in run_result.results.items():
        value = res.output.content if hasattr(res.output, "content") else res.output
        if res.success:
            out[name] = value
        else:
            out[name] = {
                "success": False,
                "output": value,
                "error": getattr(res, "error", None),
            }
    return out


def _handle_run_completion(
    run_result: Any,
    job_id: str,
    loop_name: str,
    config: Any,
    duration_ms: int,
) -> str:
    """Persist job outcome and return JSON response."""
    output_results = _format_completed_results(run_result)
    metrics = {
        "total_cost": round(run_result.total_cost, 6),
        "total_tokens": run_result.total_tokens,
        "duration_ms": duration_ms,
    }

    if run_result.success:
        rt.store.update_job(
            job_id=job_id,
            status="completed",
            results=output_results,
            metrics=metrics,
            completed=True,
        )
        return json.dumps(
            {
                "job_id": job_id,
                "status": "completed",
                "loop_name": loop_name,
                "provider": config.provider,
                "model": config.model,
                "results": output_results,
                "steps_completed": len(run_result.steps_executed),
                "total_cost": round(run_result.total_cost, 6),
                "total_tokens": run_result.total_tokens,
                "duration_ms": duration_ms,
            },
            indent=2,
        )

    rt.store.update_job(
        job_id=job_id,
        status="failed",
        results=output_results,
        error=run_result.error or "Loop execution failed",
        metrics=metrics,
        completed=True,
    )
    return json.dumps(
        {
            "job_id": job_id,
            "status": "failed",
            "loop_name": loop_name,
            "error": run_result.error or "Loop execution failed",
            "completed_steps": output_results,
            "steps_executed": run_result.steps_executed,
            "duration_ms": duration_ms,
        },
        indent=2,
    )


@mcp.tool()
def loop_run(
    loop_name: str = "",
    context: str = "{}",
    model: str | None = None,
    search_dir: str | None = None,
    spec_json: str | None = None,
    mode: str = "detached",
) -> str:
    """Execute a loop end-to-end. Returns immediately with job_id; poll loop_status for progress.

    Two sources are supported:
    - spec_json: raw LoopSpec v1 JSON string. mode='detached' (default) runs it
      in a worker thread inside this MCP process (no orphan processes; dies
      with opencode). mode='agent' only creates a ready job and returns the
      step plan: the calling agent executes steps itself and records progress
      via loop_record.
    - loop_name: a Python DSL loop discovered in the workspace (legacy path).
    """
    if spec_json is not None:
        return _run_spec_json(spec_json, context, mode)

    if not loop_name:
        return json.dumps({"error": "Provide either spec_json or loop_name."})

    config = get_llm_config(model_override=model)
    if not config:
        return json.dumps(
            {
                "error": "No LLM API key configured",
                "help": (
                    "Set one of:\n"
                    "  LOOPMASTER_LLM_API_KEY=sk-xxx\n"
                    "  LOOPMASTER_OPENAI_API_KEY=sk-xxx\n"
                    "  LOOPMASTER_ANTHROPIC_API_KEY=sk-ant-xxx\n"
                    "  LOOPMASTER_OPENROUTER_API_KEY=sk-or-xxx"
                ),
            }
        )

    target_file, target_loop_def = _find_target_loop_def(loop_name, search_dir)
    if not target_loop_def or not target_file:
        return f"Error: Loop '{loop_name}' not found."

    try:
        ctx_data = json.loads(context) if isinstance(context, str) else dict(context)
    except Exception as exc:
        return json.dumps({"error": f"Invalid context JSON: {exc}"})

    job_id = f"{loop_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    rt.store.create_job(
        job_id=job_id,
        loop_name=loop_name,
        definition=load_loop_def(target_file) or {"name": loop_name},
        status="running",
        metrics={"host_pid": os.getpid()},
    )

    cancel_event = threading.Event()
    rt.cancel_events[job_id] = cancel_event

    step_counter = {"n": 0}

    def _on_step(_result: object) -> None:
        step_counter["n"] += 1
        with contextlib.suppress(Exception):
            rt.store.update_job(job_id=job_id, current_step=step_counter["n"])

    def _run_background():
        start_time = time.time()
        engine = LoopEngine(
            budget=target_loop_def.budget,
            error_policy=ErrorPolicy(
                retry=2,
                on_failure=RecoveryAction.SKIP,
                fallback_model="@smart",
            ),
            interruption_protection=target_loop_def.interruption_protection,
            cost_tracker=CostTracker(),
            metrics_collector=MetricsCollector(),
            llm_client=LLMClient(config=config),
            cancel_event=cancel_event,
        )
        engine.on_step_complete(_on_step)
        try:
            run_result = engine.run(target_loop_def, initial_context=ctx_data)
        except Exception as exc:
            duration_ms = int((time.time() - start_time) * 1000)
            rt.store.update_job(job_id=job_id, status="failed", error=str(exc), completed=True)
            rt.cancel_events.pop(job_id, None)
            return
        duration_ms = int((time.time() - start_time) * 1000)
        rt.cancel_events.pop(job_id, None)
        _handle_run_completion(run_result, job_id, loop_name, config, duration_ms)

    thread = threading.Thread(target=_run_background, daemon=True)
    thread.start()

    return json.dumps(
        with_pending(
            {
                "job_id": job_id,
                "status": "running",
                "loop_name": loop_name,
                "message": "Loop started. Use loop_status to check progress.",
            }
        ),
        indent=2,
    )
