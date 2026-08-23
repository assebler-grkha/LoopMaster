#!/usr/bin/env python3
"""LoopMaster MCP Server — loop definitions and persistent execution engine.

Provides MCP tools to:
  1. loop_list  → discover available @Loop files
  2. loop_get   → returns full loop definition (steps, prompts, context) & registers job
  3. loop_result → report step outcomes (persisted to SQLite)
  4. loop_status → check overall progress from SQLite
  5. loop_cancel → cancel a running loop
  6. loop_run    → execute a loop end-to-end via LoopEngine with multi-provider LLM API
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from loopmaster.core.engine import LoopEngine
from loopmaster.core.types import ErrorPolicy
from loopmaster.core.types import LoopDef as LoopDefType
from loopmaster.cost.tracker import CostTracker
from loopmaster.llm import LLMClient, get_llm_config
from loopmaster.mcp.discovery import (
    find_loop_files,
    load_loop_def,
    load_loop_def_object,
    loop_def_to_dict,
)
from loopmaster.mcp.job_store import get_job_store
from loopmaster.metrics.collector import MetricsCollector

# Backwards-compatible aliases for private helpers
_find_loop_files = find_loop_files
_load_loop_def = load_loop_def
_load_loop_def_object = load_loop_def_object
_loop_def_to_dict = loop_def_to_dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loopmaster-mcp")

mcp = FastMCP(
    name="loopmaster",
    instructions=(
        "LoopMaster provides AI agent loop definitions and execution. "
        "Use loop_list to discover loops, loop_run to execute loops via LoopEngine, "
        "or loop_get + loop_result for step-by-step agent execution."
    ),
)

_store = get_job_store()
_store.mark_interrupted_jobs_on_startup()


# ── MCP Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def loop_list(search_dir: str | None = None) -> str:
    """Discover available LoopMaster loops.

    Scans the current directory (and optional search_dir) for Python files
    containing @Loop decorators. Returns loop names, versions, and step counts.
    """
    search = Path(search_dir) if search_dir else None
    files = find_loop_files(search)
    if not files:
        return "No loops found. Create a .py file with @Loop decorator."

    lines = [f"Found {len(files)} loop(s):"]
    for f in files:
        info = load_loop_def(f)
        if info:
            budget = f" | budget: {info['budget']}" if "budget" in info else ""
            count = info["step_count"]
            lines.append(
                f"  - {info['name']} v{info['version']} ({count} steps{budget}) [{f.name}]"
            )
        else:
            lines.append(f"  - {f.name} (could not load)")
    return "\n".join(lines)


@mcp.tool()
def loop_get(loop_name: str, search_dir: str | None = None) -> str:
    """Get the full definition of a loop for execution.

    Registers a job in the persistent SQLite store and returns the loop definition
    with a job_id for tracking progress.
    """
    search = Path(search_dir) if search_dir else None
    files = find_loop_files(search)

    for f in files:
        info = load_loop_def(f)
        if info and info["name"] == loop_name:
            job_id = f"{loop_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            _store.create_job(job_id=job_id, loop_name=loop_name, definition=info)
            return json.dumps({"job_id": job_id, "loop": info}, indent=2)

    return f"Error: Loop '{loop_name}' not found."


@mcp.tool()
def loop_result(
    job_id: str,
    step_name: str,
    success: bool,
    output: str | None = None,
    error: str | None = None,
    tokens_used: int = 0,
    cost: float = 0.0,
) -> str:
    """Report the result of a step execution back to LoopMaster.

    Persists the step outcome in SQLite and returns the next step to execute.
    """
    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."

    _store.record_step_result(
        job_id=job_id,
        step_name=step_name,
        success=success,
        output=output,
        error=error or "",
    )

    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found after update."

    total = job.total_steps
    done = len(job.results)

    if not success and error:
        policy = _get_error_policy(job.definition, step_name)
        return json.dumps(
            {
                "status": "error",
                "progress": f"{done}/{total}",
                "error": error,
                "suggestion": _get_recovery_suggestion(policy, error),
            },
            indent=2,
        )

    if done >= total:
        return json.dumps(
            {
                "status": "completed",
                "progress": f"{done}/{total}",
                "summary": _build_summary(job.results),
            },
            indent=2,
        )

    steps = job.definition.get("steps", [])
    next_step = steps[done] if done < len(steps) else {}
    return json.dumps(
        {
            "status": "in_progress",
            "progress": f"{done}/{total}",
            "next_step": next_step,
        },
        indent=2,
    )


@mcp.tool()
def loop_status(job_id: str) -> str:
    """Check the status of a loop execution from SQLite store."""
    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."

    total = job.total_steps
    done = len(job.results)
    return json.dumps(
        {
            "job_id": job.job_id,
            "loop_name": job.loop_name,
            "status": job.status,
            "progress": f"{done}/{total}",
            "results": job.results,
        },
        indent=2,
    )


@mcp.tool()
def loop_cancel(job_id: str) -> str:
    """Cancel a loop execution in SQLite store."""
    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."
    _store.cancel_job(job_id)
    return f"Loop '{job.loop_name}' cancelled."


# ── LLM Execution via Unified LoopEngine ────────────────────────────────────


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
    """Format step results into serializable dict."""
    return {
        name: (res.output.content if hasattr(res.output, "content") else res.output)
        for name, res in run_result.results.items()
        if res.success
    }


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
        _store.update_job(
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

    _store.update_job(
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
    loop_name: str,
    context: str = "{}",
    model: str | None = None,
    search_dir: str | None = None,
) -> str:
    """Execute a loop end-to-end using LoopEngine and multi-provider LLM API."""
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
    _store.create_job(
        job_id=job_id,
        loop_name=loop_name,
        definition=load_loop_def(target_file) or {"name": loop_name},
        status="running",
    )

    start_time = time.time()
    engine = LoopEngine(
        budget=target_loop_def.budget,
        error_policy=ErrorPolicy(),
        interruption_protection=target_loop_def.interruption_protection,
        cost_tracker=CostTracker(),
        metrics_collector=MetricsCollector(),
        llm_client=LLMClient(config=config),
    )

    try:
        run_result = engine.run(target_loop_def, initial_context=ctx_data)
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        _store.update_job(job_id=job_id, status="failed", error=str(exc), completed=True)
        return json.dumps(
            {
                "job_id": job_id,
                "status": "failed",
                "loop_name": loop_name,
                "error": str(exc),
                "duration_ms": duration_ms,
            },
            indent=2,
        )

    duration_ms = int((time.time() - start_time) * 1000)
    return _handle_run_completion(run_result, job_id, loop_name, config, duration_ms)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_error_policy(loop_def: dict[str, Any], step_name: str) -> dict[str, Any]:
    """Get error policy for a step."""
    for step in loop_def.get("steps", []):
        if step["name"] == step_name and "on_error" in step:
            res: dict[str, Any] = step["on_error"]
            return res
    return {"retry": 2, "on_failure": "abort"}


def _get_recovery_suggestion(policy: dict[str, Any], error: str) -> str:
    """Suggest recovery action based on error policy."""
    action = policy.get("on_failure", "abort")
    retry = policy.get("retry", 2)
    if action == "retry":
        return f"Retry the step (up to {retry} times). Error: {error}"
    if action == "skip":
        return f"Skip this step and continue. Error: {error}"
    if action == "fallback":
        fb = policy.get("fallback_model", "a different model")
        return f"Retry with {fb}. Error: {error}"
    return f"Abort the loop. Error: {error}"


def _build_summary(results: dict[str, Any]) -> str:
    """Build execution summary from results dict."""
    total = len(results)
    succeeded = sum(1 for r in results.values() if r.get("success"))
    failed = total - succeeded
    return f"Completed {succeeded}/{total} steps" + (f" ({failed} failed)" if failed else "")


if __name__ == "__main__":
    mcp.run(transport="stdio")
