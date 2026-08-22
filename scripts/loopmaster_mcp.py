#!/usr/bin/env python3
"""LoopMaster MCP Server — loop definitions and persistent execution engine.

Provides MCP tools to:
  1. loop_list  → discover available @Loop files
  2. loop_get   → returns full loop definition (steps, prompts, context) & registers job
  3. loop_result → report step outcomes (persisted to SQLite)
  4. loop_status → check overall progress from SQLite
  5. loop_cancel → cancel a running loop
  6. loop_run    → execute a loop end-to-end via LoopEngine with multi-provider LLM API

Usage:
    python scripts/loopmaster_mcp.py          # stdio mode (for MCP)
    python scripts/loopmaster_mcp.py --help   # show help
"""

import importlib.util
import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastmcp import FastMCP

from loopmaster.core.engine import LoopEngine
from loopmaster.core.types import ErrorPolicy
from loopmaster.core.types import LoopDef as LoopDefType
from loopmaster.cost.tracker import CostTracker
from loopmaster.llm import LLMClient, get_llm_config
from loopmaster.mcp.job_store import get_job_store
from loopmaster.metrics.collector import MetricsCollector

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

# ── Persistent Job Storage ──────────────────────────────────────────────────

_store = get_job_store()
_store.mark_interrupted_jobs_on_startup()

# ── Loop discovery ───────────────────────────────────────────────────────────


def _find_loop_files(search_dir: Path | None = None) -> list[Path]:
    """Find .py files containing @Loop decorators."""
    dirs = []
    if search_dir and search_dir.is_dir():
        dirs.append(search_dir)
    cwd = Path.cwd()
    if cwd not in dirs:
        dirs.append(cwd)
    env_dir = os.environ.get("LOOPMASTER_LOOPS_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir() and p not in dirs:
            dirs.append(p)

    results = []
    seen = set()
    for d in dirs:
        for py_file in d.rglob("*.py"):
            if py_file.name.startswith("_") or py_file in seen:
                continue
            seen.add(py_file)
            try:
                content = py_file.read_text(encoding="utf-8")
                if "@Loop" in content or "from loopmaster" in content:
                    results.append(py_file)
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Skipping unreadable file %s: %s", py_file, exc)
    return results


def _load_loop_def_object(py_file: Path) -> LoopDefType | None:
    """Load raw LoopDef instance from a Python file."""
    try:
        spec = importlib.util.spec_from_file_location("_loop_mod", py_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, LoopDefType):
                return attr
            if hasattr(attr, "_loop_def") and isinstance(attr._loop_def, LoopDefType):
                return attr._loop_def
    except Exception as exc:
        logger.debug("Failed to load LoopDef object from %s: %s", py_file, exc)
    return None


def _load_loop_def(py_file: Path) -> dict | None:
    """Load a LoopDef from a Python file and return it as a dict."""
    loop_def = _load_loop_def_object(py_file)
    if loop_def is not None:
        return _loop_def_to_dict(loop_def, py_file)
    return None


def _loop_def_to_dict(loop_def: LoopDefType, source_file: Path) -> dict:
    """Convert LoopDef to a serializable dict with full step info."""
    from loopmaster.core.context import Context
    from loopmaster.core.engine import _set_current_steps

    steps = []
    try:
        collected = []
        _set_current_steps(collected)
        ctx = Context({})
        ctx._loop_engine = None
        ctx._executed_steps = []
        ctx._results = {}
        ctx._current_error_policy = None
        loop_def.body(ctx)
        for s in collected:
            step_info = {"name": s.name}
            if s.model:
                step_info["model"] = s.model
            if s.tool:
                step_info["tool"] = s.tool
            if s.prompt:
                step_info["prompt"] = s.prompt
            if s.input is not None:
                step_info["input"] = s.input
            if s.retry is not None:
                step_info["retry"] = s.retry
            if s.timeout is not None:
                step_info["timeout"] = s.timeout
            if s.on_error:
                step_info["on_error"] = s.on_error.to_dict()
            steps.append(step_info)
    except Exception as exc:
        logger.warning("Could not collect steps from %s: %s", loop_def.name, exc)
    finally:
        _set_current_steps(None)

    result = {
        "name": loop_def.name,
        "version": loop_def.version,
        "source_file": str(source_file),
        "steps": steps,
        "step_count": len(steps),
    }
    if loop_def.agent:
        result["agent"] = loop_def.agent
    if loop_def.budget:
        result["budget"] = loop_def.budget.to_dict()
    if loop_def.interruption_protection:
        result["interruption_protection"] = loop_def.interruption_protection.to_dict()
    return result


# ── MCP Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def loop_list(search_dir: str | None = None) -> str:
    """Discover available LoopMaster loops.

    Scans the current directory (and optional search_dir) for Python files
    containing @Loop decorators. Returns loop names, versions, and step counts.

    Args:
        search_dir: Optional directory to search for loop files.
    """
    search = Path(search_dir) if search_dir else None
    files = _find_loop_files(search)
    if not files:
        return "No loops found. Create a .py file with @Loop decorator."

    lines = [f"Found {len(files)} loop(s):"]
    for f in files:
        info = _load_loop_def(f)
        if info:
            budget = f" | budget: {info['budget']}" if "budget" in info else ""
            lines.append(
                f"  - {info['name']} v{info['version']} "
                f"({info['step_count']} steps{budget}) [{f.name}]"
            )
        else:
            lines.append(f"  - {f.name} (could not load)")
    return "\n".join(lines)


@mcp.tool()
def loop_get(loop_name: str, search_dir: str | None = None) -> str:
    """Get the full definition of a loop for execution.

    Returns step-by-step instructions including prompts, models, tools,
    error policies, and budget. OpenCode should execute each step
    using its own LLM capabilities.

    Args:
        loop_name: Name of the loop to retrieve.
        search_dir: Optional directory to search for loop files.
    """
    search = Path(search_dir) if search_dir else None
    files = _find_loop_files(search)

    for f in files:
        info = _load_loop_def(f)
        if info and info["name"] == loop_name:
            job_id = f"{loop_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            _store.create_job(
                job_id=job_id,
                loop_name=loop_name,
                definition=info,
                status="ready",
                total_steps=info["step_count"],
            )
            return json.dumps(
                {
                    "job_id": job_id,
                    "loop": info,
                    "instructions": (
                        "Execute each step in order. For each step:\n"
                        "1. If step has 'prompt' and 'model' → use your LLM to generate output\n"
                        "2. If step has 'tool' and 'input' → call the specified tool\n"
                        "3. Pass outputs between steps using the step name as variable\n"
                        "4. Report each step result with loop_result\n"
                        "5. If a step has 'on_error' policy, follow it on failure"
                    ),
                },
                indent=2,
            )

    return f"Error: Loop '{loop_name}' not found. Use loop_list to see available loops."


@mcp.tool()
def loop_result(
    job_id: str,
    step_name: str,
    success: bool,
    output: str = "",
    error: str = "",
) -> str:
    """Report the result of executing a step.

    Called by OpenCode after executing each step of a loop. Results are persisted
    atomically to SQLite.

    Args:
        job_id: The job ID from loop_get.
        step_name: Name of the step that was executed.
        success: Whether the step succeeded.
        output: The step output (if success=True).
        error: The error message (if success=False).
    """
    job = _store.record_step_result(
        job_id=job_id,
        step_name=step_name,
        success=success,
        output=output,
        error=error,
    )
    if not job:
        return f"Error: Job '{job_id}' not found."

    total = job.total_steps
    done = len(job.results)
    failed = sum(1 for r in job.results.values() if not r["success"])

    if failed > 0:
        policy = _get_error_policy(job.definition, step_name)
        return json.dumps(
            {
                "status": "error",
                "step": step_name,
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
    """Check the status of a loop execution from SQLite store.

    Args:
        job_id: The job ID to check.
    """
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
    """Cancel a loop execution in SQLite store.

    Args:
        job_id: The job ID to cancel.
    """
    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."
    _store.cancel_job(job_id)
    return f"Loop '{job.loop_name}' cancelled."


# ── LLM Execution via Unified LoopEngine ────────────────────────────────────


@mcp.tool()
def loop_run(
    loop_name: str,
    context: str = "{}",
    model: str | None = None,
    search_dir: str | None = None,
) -> str:
    """Execute a loop end-to-end using LoopEngine and multi-provider LLM API.

    Requires LOOPMASTER_LLM_API_KEY env var (or LOOPMASTER_<PROVIDER>_API_KEY).
    Supported providers: openai, anthropic, google, openrouter, custom.

    Args:
        loop_name: Name of the loop to execute.
        context: JSON string with input variables for the loop.
        model: Override the default model for all steps.
        search_dir: Optional directory to search for loop files.
    """
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

    search = Path(search_dir) if search_dir else None
    files = _find_loop_files(search)
    target_file = None
    target_loop_def = None

    for f in files:
        ldef = _load_loop_def_object(f)
        if ldef and ldef.name == loop_name:
            target_file = f
            target_loop_def = ldef
            break

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
        definition=_load_loop_def(target_file) or {"name": loop_name},
        status="running",
    )

    start_time = time.time()
    cost_tracker = CostTracker()
    metrics_collector = MetricsCollector()
    llm_client = LLMClient(config=config)

    engine = LoopEngine(
        budget=target_loop_def.budget,
        error_policy=ErrorPolicy(),
        interruption_protection=target_loop_def.interruption_protection,
        cost_tracker=cost_tracker,
        metrics_collector=metrics_collector,
        llm_client=llm_client,
    )

    try:
        run_result = engine.run(target_loop_def, initial_context=ctx_data)
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        _store.update_job(
            job_id=job_id,
            status="failed",
            error=str(exc),
            completed=True,
        )
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
    output_results = {
        name: (res.output.content if hasattr(res.output, "content") else res.output)
        for name, res in run_result.results.items()
        if res.success
    }

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
    else:
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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_error_policy(loop_def: dict, step_name: str) -> dict:
    """Get error policy for a step."""
    for step in loop_def.get("steps", []):
        if step["name"] == step_name and "on_error" in step:
            return step["on_error"]
    return {"retry": 2, "on_failure": "abort"}


def _get_recovery_suggestion(policy: dict, error: str) -> str:
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


def _build_summary(results: dict) -> str:
    """Build execution summary from results dict."""
    total = len(results)
    succeeded = sum(1 for r in results.values() if r["success"])
    failed = total - succeeded
    return f"Completed {succeeded}/{total} steps" + (f" ({failed} failed)" if failed else "")


if __name__ == "__main__":
    mcp.run(transport="stdio")
