#!/usr/bin/env python3
"""LoopMaster MCP Server — loop definitions for OpenCode execution.

OpenCode IS the LLM provider. This server exposes loop definitions
as structured data. OpenCode reads the definition, executes each step
using its own LLM capabilities, and reports results back.

Flow:
  1. loop_list  → discover available @Loop files
  2. loop_start → returns full loop definition (steps, prompts, context)
  3. OpenCode executes each step using its own model
  4. loop_result → OpenCode reports step outcomes
  5. loop_status → check overall progress

Usage:
    python scripts/loopmaster_mcp.py          # stdio mode (for MCP)
    python scripts/loopmaster_mcp.py --help   # show help
"""

import importlib.util
import json
import logging
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loopmaster-mcp")

try:
    from fastmcp import FastMCP
except ImportError:
    logger.exception("fastmcp not installed")
    print("fastmcp not installed. Run: pip install fastmcp")
    raise

mcp = FastMCP(
    name="loopmaster",
    instructions=(
        "LoopMaster provides AI agent loop definitions. "
        "Use loop_list to discover loops, loop_start to get the full definition "
        "(steps, prompts, error policies), then execute each step yourself. "
        "Report results with loop_result."
    ),
)

# ── In-memory job tracking ──────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ── Loop discovery ───────────────────────────────────────────────────────────

def _find_loop_files(search_dir: Path | None = None) -> list[Path]:
    """Find .py files containing @Loop decorators."""
    import os
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
            except Exception:
                pass
    return results


def _load_loop_def(py_file: Path) -> dict | None:
    """Load a LoopDef from a Python file and return it as a dict."""
    try:
        spec = importlib.util.spec_from_file_location("_loop_mod", py_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from loopmaster.core.types import LoopDef as LoopDefType

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            loop_def = None
            if isinstance(attr, LoopDefType):
                loop_def = attr
            elif hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
            if loop_def is not None:
                return _loop_def_to_dict(loop_def, py_file)
    except Exception as exc:
        logger.debug("Failed to load %s: %s", py_file, exc)
    return None


def _loop_def_to_dict(loop_def, source_file: Path) -> dict:
    """Convert LoopDef to a serializable dict with full step info."""
    # Collect steps by running the body in collection mode
    from loopmaster.core.engine import _set_current_steps
    from loopmaster.core.types import Context

    steps = []
    try:
        collected = []
        _set_current_steps(collected)
        ctx = Context({})
        ctx._loop_engine = None
        ctx._executed_steps = set()
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
            # Create a job to track this execution
            job_id = f"{loop_name}_{int(time.time())}"
            with _jobs_lock:
                _jobs[job_id] = {
                    "loop_name": loop_name,
                    "definition": info,
                    "status": "ready",
                    "current_step": 0,
                    "results": {},
                    "started_at": time.time(),
                }
            return json.dumps({
                "job_id": job_id,
                "loop": info,
                "instructions": (
                    "Execute each step in order. For each step:\n"
                    "1. If step has 'prompt' and 'model' → use your LLM to generate a response\n"
                    "2. If step has 'tool' and 'input' → call the specified tool\n"
                    "3. Pass outputs between steps using the step name as variable\n"
                    "4. Report each step result with loop_result\n"
                    "5. If a step has 'on_error' policy, follow it on failure"
                ),
            }, indent=2)

    return f"Error: Loop '{loop_name}' not found. Use loop_list to see available loops."


@mcp.tool()
def loop_result(job_id: str, step_name: str, success: bool, output: str = "", error: str = "") -> str:
    """Report the result of executing a step.

    Called by OpenCode after executing each step of a loop.

    Args:
        job_id: The job ID from loop_get.
        step_name: Name of the step that was executed.
        success: Whether the step succeeded.
        output: The step output (if success=True).
        error: The error message (if success=False).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return f"Error: Job '{job_id}' not found."

        job["results"][step_name] = {
            "success": success,
            "output": output,
            "error": error,
            "timestamp": time.time(),
        }
        job["current_step"] += 1

        total = job["definition"]["step_count"]
        done = len(job["results"])
        failed = sum(1 for r in job["results"].values() if not r["success"])

        if failed > 0:
            policy = _get_error_policy(job["definition"], step_name)
            job["status"] = "error"
            return json.dumps({
                "status": "error",
                "step": step_name,
                "progress": f"{done}/{total}",
                "error": error,
                "suggestion": _get_recovery_suggestion(policy, error),
            }, indent=2)

        if done >= total:
            job["status"] = "completed"
            return json.dumps({
                "status": "completed",
                "progress": f"{done}/{total}",
                "summary": _build_summary(job),
            }, indent=2)

        next_step = job["definition"]["steps"][done]
        job["status"] = "running"
        return json.dumps({
            "status": "in_progress",
            "progress": f"{done}/{total}",
            "next_step": next_step,
        }, indent=2)


@mcp.tool()
def loop_status(job_id: str) -> str:
    """Check the status of a loop execution.

    Args:
        job_id: The job ID to check.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return f"Error: Job '{job_id}' not found."

        total = job["definition"]["step_count"]
        done = len(job["results"])
        return json.dumps({
            "job_id": job_id,
            "loop_name": job["loop_name"],
            "status": job["status"],
            "progress": f"{done}/{total}",
            "results": job["results"],
        }, indent=2)


@mcp.tool()
def loop_cancel(job_id: str) -> str:
    """Cancel a loop execution.

    Args:
        job_id: The job ID to cancel.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return f"Error: Job '{job_id}' not found."
        job["status"] = "cancelled"
        return f"Loop '{job['loop_name']}' cancelled."


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_error_policy(loop_def: dict, step_name: str) -> dict:
    """Get error policy for a step."""
    for step in loop_def["steps"]:
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


def _build_summary(job: dict) -> str:
    """Build execution summary."""
    results = job["results"]
    total = len(results)
    succeeded = sum(1 for r in results.values() if r["success"])
    failed = total - succeeded
    return f"Completed {succeeded}/{total} steps" + (f" ({failed} failed)" if failed else "")


if __name__ == "__main__":
    mcp.run(transport="stdio")
