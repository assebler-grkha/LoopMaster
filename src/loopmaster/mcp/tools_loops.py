"""Loop discovery and lifecycle MCP tools."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.discovery import (
    build_summary as _build_summary,
)
from loopmaster.mcp.discovery import (
    find_loop_files,
    load_loop_def,
)
from loopmaster.mcp.discovery import (
    get_error_policy as _get_error_policy,
)
from loopmaster.mcp.discovery import (
    get_recovery_suggestion as _get_recovery_suggestion,
)
from loopmaster.mcp.job_store import is_pid_alive
from loopmaster.mcp.runtime import mcp
from loopmaster.mcp.tools_blocks import validate_code_refs
from loopmaster.mcp.tools_hitl import _question_view
from loopmaster.mcp.tools_notifications import with_pending
from loopmaster.spec import SpecValidationError, load_loop_from_dict


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
            rt.store.create_job(job_id=job_id, loop_name=loop_name, definition=info)
            return json.dumps(with_pending({"job_id": job_id, "loop": info}), indent=2)

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
    job = rt.store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."

    rt.store.record_step_result(
        job_id=job_id,
        step_name=step_name,
        success=success,
        output=output,
        error=error or "",
    )

    job = rt.store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found after update."

    total = job.total_steps
    done = len(job.results)

    if not success and error:
        policy = _get_error_policy(job.definition, step_name)
        return json.dumps(
            with_pending(
                {
                    "status": "error",
                    "progress": f"{done}/{total}",
                    "error": error,
                    "suggestion": _get_recovery_suggestion(policy, error),
                }
            ),
            indent=2,
        )

    if done >= total:
        return json.dumps(
            with_pending(
                {
                    "status": "completed",
                    "progress": f"{done}/{total}",
                    "summary": _build_summary(job.results),
                }
            ),
            indent=2,
        )

    steps = job.definition.get("steps", [])
    next_step = steps[done] if done < len(steps) else {}
    return json.dumps(
        with_pending(
            {
                "status": "in_progress",
                "progress": f"{done}/{total}",
                "next_step": next_step,
            }
        ),
        indent=2,
    )


@mcp.tool()
def loop_status(job_id: str) -> str:
    """Check the status of a loop execution from SQLite store."""
    job = rt.store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."

    if job.status in ("running", "in_progress"):
        # waiting_input is excluded: HITL waits are idle by design and get no
        # heartbeats, so a stale check there would fail live jobs.
        metrics = job.metrics or {}
        host_pid = metrics.get("host_pid")
        owner_dead = host_pid is not None and host_pid != os.getpid() and not is_pid_alive(host_pid)
        # Agent-driven jobs have no watcher/heartbeat: long gaps between
        # loop_record calls are legitimate, so only the owner check applies.
        stale = time.time() - job.updated_at > 900 and metrics.get("execution") != "agent"
        if owner_dead or stale:
            reason = (
                f"Server process (pid {host_pid}) exited before completion"
                if owner_dead
                else "Job stale: no progress update for more than 15 minutes"
            )
            rt.store.update_job(job_id=job_id, status="failed", error=reason, completed=True)
            job = rt.store.get_job(job_id) or job

    total = job.total_steps
    done = max(job.current_step, len(job.results))
    payload = {
        "job_id": job.job_id,
        "loop_name": job.loop_name,
        "status": job.status,
        "progress": f"{done}/{total}",
        "results": job.results,
    }
    if job.error:
        payload["error"] = job.error
    if job.metrics:
        payload["metrics"] = job.metrics
    if job.status == "waiting_input":
        payload["questions"] = [_question_view(m) for m in rt.store.list_questions(job_id=job_id)]
        payload["message"] = (
            "Loop is waiting for input. Answer with loop_respond(job_id, msg_id, answer)."
        )
    return json.dumps(with_pending(payload), indent=2)


@mcp.tool()
def loop_cancel(job_id: str) -> str:
    """Cancel a running loop execution."""
    job = rt.store.get_job(job_id)
    if not job:
        return f"Error: Job '{job_id}' not found."
    # Persist 'cancelled' BEFORE setting the in-process event: the worker's
    # except-branch reports cancelled only when it observes the flag, and a
    # persisted failure first would make cancel_job refuse the update.
    rt.store.cancel_job(job_id)
    rt.runner.request_cancel(job_id)
    cancel_event = rt.cancel_events.get(job_id)
    if cancel_event:
        cancel_event.set()
    return json.dumps(
        with_pending({"cancelled": True, "job_id": job_id, "loop_name": job.loop_name}),
        indent=2,
    )


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
            {"error": f"name mismatch: loop_name='{loop_name}' but spec name='{data.get('name')}'"}
        )

    try:
        loop_def, _spec = load_loop_from_dict(data)
    except SpecValidationError as exc:
        return json.dumps({"error": str(exc)})

    ref_error = validate_code_refs(data)
    if ref_error:
        return json.dumps({"error": ref_error})

    saved = rt.store.save_loop(
        name=loop_name,
        version=loop_def.version,
        spec=data,
        source_hash=loop_def.source_hash,
    )
    return json.dumps(
        with_pending(
            {
                "saved": True,
                "name": saved.name,
                "version": saved.version,
                "source_hash": saved.source_hash[:16],
                "steps": _spec.step_names(),
                "message": "Use loop_run with this loop_name or the raw spec_json.",
            }
        ),
        indent=2,
    )


@mcp.tool()
def loop_delete(loop_name: str) -> str:
    """Delete a persisted JSON loop spec from the database."""
    if rt.store.delete_loop(loop_name):
        return json.dumps(with_pending({"deleted": True, "name": loop_name}), indent=2)
    return json.dumps(
        with_pending({"deleted": False, "error": f"Loop '{loop_name}' not found in store."}),
        indent=2,
    )
