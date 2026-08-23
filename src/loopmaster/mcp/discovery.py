"""Loop discovery and inspection for MCP servers and tooling."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from loopmaster.core.types import LoopDef as LoopDefType

logger = logging.getLogger("loopmaster.mcp.discovery")


def find_loop_files(search_dir: Path | None = None) -> list[Path]:
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


def load_loop_def_object(py_file: Path) -> LoopDefType | None:
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


def _format_step_or_block(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    step_info: dict[str, Any] = {"name": getattr(item, "name", "step")}
    for field in ("model", "tool", "prompt", "input", "retry", "timeout"):
        val = getattr(item, field, None)
        if val is not None:
            step_info[field] = val
    if getattr(item, "on_error", None):
        step_info["on_error"] = item.on_error.to_dict()
    return step_info


def loop_def_to_dict(loop_def: LoopDefType, source_file: Path) -> dict[str, Any]:
    """Convert LoopDef to a serializable dict with full step info."""
    from loopmaster.core.context import Context
    from loopmaster.core.engine import _set_current_steps

    steps: list[dict[str, Any]] = []
    try:
        collected: list[Any] = []
        _set_current_steps(collected)
        ctx = Context({})
        ctx._loop_engine = None
        ctx._executed_steps = []
        ctx._results = {}
        ctx._current_error_policy = None
        loop_def.body(ctx)
        for s in collected:
            steps.append(_format_step_or_block(s))
    except Exception as exc:
        logger.warning("Could not collect steps from %s: %s", loop_def.name, exc)
    finally:
        _set_current_steps(None)

    result: dict[str, Any] = {
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


def load_loop_def(py_file: Path) -> dict[str, Any] | None:
    """Load a LoopDef from a Python file and return it as a dict."""
    loop_def = load_loop_def_object(py_file)
    if loop_def is not None:
        return loop_def_to_dict(loop_def, py_file)
    return None


def get_error_policy(loop_def: dict[str, Any], step_name: str) -> dict[str, Any]:
    """Get error policy for a step."""
    for step in loop_def.get("steps", []):
        if step.get("name") == step_name and "on_error" in step:
            res: dict[str, Any] = step["on_error"]
            return res
    return {"retry": 2, "on_failure": "abort"}


def get_recovery_suggestion(policy: dict[str, Any], error: str) -> str:
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


def build_summary(results: dict[str, Any]) -> str:
    """Build execution summary from results dict."""
    total = len(results)
    succeeded = sum(1 for r in results.values() if r.get("success"))
    failed = total - succeeded
    return f"Completed {succeeded}/{total} steps" + (f" ({failed} failed)" if failed else "")
