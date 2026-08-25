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
import os
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _pid_alive(pid: int | None) -> bool:
    """Return True if a process with the given PID exists on this host."""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError):
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


from fastmcp import FastMCP

from loopmaster.core.engine import LoopEngine
from loopmaster.core.policies import RecoveryAction
from loopmaster.core.types import ErrorPolicy
from loopmaster.core.types import LoopDef as LoopDefType
from loopmaster.cost.tracker import CostTracker
from loopmaster.llm import LLMClient, get_llm_config
from loopmaster.mcp.discovery import (
    build_summary as _build_summary,
)
from loopmaster.mcp.discovery import (
    find_loop_files,
    load_loop_def,
    load_loop_def_object,
    loop_def_to_dict,
)
from loopmaster.mcp.discovery import (
    get_error_policy as _get_error_policy,
)
from loopmaster.mcp.discovery import (
    get_recovery_suggestion as _get_recovery_suggestion,
)
from loopmaster.mcp.job_store import get_job_store
from loopmaster.mcp.models_tools import handle_model_list, handle_model_recommend
from loopmaster.mcp.worker import DetachedRunner
from loopmaster.metrics.collector import MetricsCollector
from loopmaster.spec import SpecValidationError, load_loop_from_dict

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
_runner = DetachedRunner(_store)

# Global map of active cancel events keyed by job_id
_cancel_events: dict[str, threading.Event] = {}


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

    if job.status == "running":
        host_pid = (job.metrics or {}).get("host_pid")
        owner_dead = host_pid is not None and host_pid != os.getpid() and not _pid_alive(host_pid)
        stale = time.time() - job.updated_at > 900
        if owner_dead:
            _store.update_job(
                job_id=job_id,
                status="failed",
                error=f"Server process (pid {host_pid}) exited before completion",
                completed=True,
            )
            job = _store.get_job(job_id) or job
        elif stale:
            _store.update_job(
                job_id=job_id,
                status="failed",
                error="Job stale: no progress update for more than 15 minutes",
                completed=True,
            )
            job = _store.get_job(job_id) or job

    total = job.total_steps
    done = max(job.current_step, len(job.results))
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
    """Cancel a running loop execution."""
    job = _store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."
    # Persist 'cancelled' BEFORE setting the in-process event: the worker's
    # except-branch reports cancelled only when it observes the flag, and a
    # persisted failure first would make cancel_job refuse the update.
    _store.cancel_job(job_id)
    _runner.request_cancel(job_id)
    cancel_event = _cancel_events.get(job_id)
    if cancel_event:
        cancel_event.set()
    return f"Loop '{job.loop_name}' cancelled."


@mcp.tool()
def loop_save(loop_name: str, spec_json: str) -> str:
    """Validate and persist a JSON LoopSpec into the database for later runs.

    Args:
        loop_name: Storage name (must match spec "name" field).
        spec_json: Full LoopSpec v1 JSON document as a string.
    """
    try:
        data = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    if isinstance(data, dict) and data.get("name") != loop_name:
        return json.dumps(
            {
                "error": (
                    f"name mismatch: loop_name='{loop_name}' but spec name='{data.get('name')}'"
                )
            }
        )

    try:
        loop_def, _spec = load_loop_from_dict(data)
    except SpecValidationError as exc:
        return json.dumps({"error": str(exc)})

    ref_error = _validate_code_refs(data)
    if ref_error:
        return json.dumps({"error": ref_error})

    saved = _store.save_loop(
        name=loop_name,
        version=loop_def.version,
        spec=data,
        source_hash=loop_def.source_hash,
    )
    return json.dumps(
        {
            "saved": True,
            "name": saved.name,
            "version": saved.version,
            "source_hash": saved.source_hash[:16],
            "steps": _spec.step_names(),
            "message": "Use loop_run with this loop_name or the raw spec_json.",
        },
        indent=2,
    )


@mcp.tool()
def loop_delete(loop_name: str) -> str:
    """Delete a persisted JSON loop spec from the database."""
    if _store.delete_loop(loop_name):
        return json.dumps({"deleted": True, "name": loop_name})
    return json.dumps({"deleted": False, "error": f"Loop '{loop_name}' not found in store."})


def _iter_code_nodes(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every code-type node in a raw LoopSpec structure."""
    if not isinstance(node, dict):
        return
    if node.get("type") == "code":
        yield node
    for key in ("steps", "then", "else"):
        child = node.get(key)
        if isinstance(child, list):
            for item in child:
                yield from _iter_code_nodes(item)


def _validate_code_refs(data: dict[str, Any]) -> str | None:
    """Check that referenced code blocks exist and sha256 pins match."""
    deny = data.get("deny_capabilities") or []
    for node in _iter_code_nodes(data.get("steps") or []):
        ref = node.get("ref", "")
        block = _store.get_code_block(ref)
        if block is None:
            return f"code step '{node.get('name')}' references unknown block '{ref}'"
        pin = node.get("sha256")
        if pin and pin != block.sha256:
            return (
                f"code step '{node.get('name')}': pinned sha256 {pin[:12]}… "
                f"does not match stored {block.sha256[:12]}…"
            )
        denied = set(block.capabilities) & set(deny)
        if denied:
            return (
                f"code block '{ref}' requires denied capabilities: "
                f"{sorted(denied)} (spec deny_capabilities)"
            )
    return None


@mcp.tool()
def block_add(
    name: str,
    version: str,
    language: str,
    source: str,
    capabilities: str = "",
    description: str = "",
) -> str:
    """Register an immutable code block (python or shell) into the store.

    Args:
        name: Kebab-case identifier, e.g. 'test-fixer'.
        version: Semantic version, e.g. '1.0.0'. Same (name, version) is immutable.
        language: 'python' or 'shell'.
        source: Full source code of the block.
        capabilities: Comma-separated: net, fs:read:<prefix>, fs:write:<prefix>.
        description: Human-readable summary.
    """
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
    try:
        block = _store.save_code_block(
            name=name,
            version=version,
            language=language,
            source=source,
            capabilities=caps,
            description=description,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(
        {
            "added": True,
            "ref": f"{block.name}@{block.version}",
            "sha256": block.sha256,
            "capabilities": block.capabilities,
            "message": (
                'Pin this block with "sha256": "' + block.sha256 + '" to guarantee immutability.'
            ),
        },
        indent=2,
    )


@mcp.tool()
def block_get(ref: str) -> str:
    """Fetch code-block metadata and source by 'name@X.Y.Z' ref."""
    block = _store.get_code_block(ref)
    if block is None:
        return json.dumps({"error": f"code block '{ref}' not found"})
    return json.dumps(
        {**block.to_dict(), "verified_sha256": _store.verify_code_block(ref)}, indent=2
    )


@mcp.tool()
def block_list(pattern: str | None = None) -> str:
    """List registered code blocks (metadata only, no source)."""
    blocks = _store.list_code_blocks(pattern=pattern)
    return json.dumps(
        {
            "count": len(blocks),
            "blocks": [b.to_dict(include_source=False) for b in blocks],
        },
        indent=2,
    )


# ── LLM Execution via Unified LoopEngine ────────────────────────────────────


def _run_spec_json(spec_json: str, context: str, mode: str) -> str:
    """Run a LoopSpec v1 JSON document through the detached worker."""
    if mode != "detached":
        return json.dumps(
            {
                "error": f"mode '{mode}' is not supported for spec_json; use mode='detached'.",
            }
        )

    try:
        data = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    try:
        loop_def, spec = load_loop_from_dict(data)
    except SpecValidationError as exc:
        return json.dumps({"error": str(exc)})

    ref_error = _validate_code_refs(data)
    if ref_error:
        return json.dumps({"error": ref_error})

    try:
        ctx_data = json.loads(context) if isinstance(context, str) else dict(context)
    except Exception as exc:
        return json.dumps({"error": f"Invalid context JSON: {exc}"})

    job_id = _runner.submit(
        loop_def,
        initial_context=ctx_data or dict(spec.initial_context),
        definition={"spec": data, "step_count": len(spec.step_names())},
    )
    return json.dumps(
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
        },
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
    loop_name: str = "",
    context: str = "{}",
    model: str | None = None,
    search_dir: str | None = None,
    spec_json: str | None = None,
    mode: str = "detached",
) -> str:
    """Execute a loop end-to-end. Returns immediately with job_id; poll loop_status for progress.

    Two sources are supported:
    - spec_json: raw LoopSpec v1 JSON string; runs detached in a worker thread
      inside this MCP process (no orphan processes; dies with opencode).
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
    _store.create_job(
        job_id=job_id,
        loop_name=loop_name,
        definition=load_loop_def(target_file) or {"name": loop_name},
        status="running",
    )
    _store.update_job(job_id=job_id, metrics={"host_pid": os.getpid()})

    cancel_event = threading.Event()
    _cancel_events[job_id] = cancel_event

    step_counter = {"n": 0}

    def _on_step(_result: object) -> None:
        step_counter["n"] += 1
        try:
            _store.update_job(job_id=job_id, current_step=step_counter["n"])
        except Exception:  # noqa: BLE001 — progress updates must never kill the run
            pass

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
            _store.update_job(job_id=job_id, status="failed", error=str(exc), completed=True)
            _cancel_events.pop(job_id, None)
            return
        duration_ms = int((time.time() - start_time) * 1000)
        _cancel_events.pop(job_id, None)
        _handle_run_completion(run_result, job_id, loop_name, config, duration_ms)

    thread = threading.Thread(target=_run_background, daemon=True)
    thread.start()

    return json.dumps(
        {
            "job_id": job_id,
            "status": "running",
            "loop_name": loop_name,
            "message": "Loop started. Use loop_status to check progress.",
        },
        indent=2,
    )


@mcp.tool()
def model_list() -> str:
    """List all registered and approved LLM models, semantic aliases, and pricing."""
    return json.dumps(handle_model_list(), indent=2)


@mcp.tool()
def model_recommend(
    task: str = "", prompt_tokens: int = 0, remaining_budget: float | None = None
) -> str:
    """Recommend the optimal approved model based on task complexity and budget constraints."""
    return json.dumps(handle_model_recommend(task, prompt_tokens, remaining_budget), indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
