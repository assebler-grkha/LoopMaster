"""JSON LoopSpec plan rendering and loading for the CLI."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console

console = Console()


def _walk_spec_nodes(nodes: list, depth: int) -> None:
    from loopmaster.core.types import Conditional, Parallel

    pad = "  " * depth
    for node in nodes:
        if isinstance(node, Parallel):
            console.print(f"{pad}• parallel ({len(node.steps)} steps)")
            _walk_spec_nodes(node.steps, depth + 1)
        elif isinstance(node, Conditional):
            cond = node.condition if isinstance(node.condition, str) else "<expr>"
            console.print(f"{pad}• conditional [{cond}]")
            console.print(f"{pad}  then:")
            _walk_spec_nodes(node.then_steps, depth + 2)
            if node.else_steps:
                console.print(f"{pad}  else:")
                _walk_spec_nodes(node.else_steps, depth + 2)
        else:
            detail = getattr(node, "model", None) or getattr(node, "tool", None) or ""
            line = f"{pad}• {node.name}"
            if detail:
                line += f" [{detail}]"
            console.print(line)


def _format_budget(budget: Any) -> str | None:
    parts = [
        f"max_cost=${budget.max_cost}" if budget.max_cost else None,
        f"max_tokens={budget.max_tokens}" if budget.max_tokens else None,
        f"max_steps={budget.max_steps}" if budget.max_steps else None,
    ]
    rendered = ", ".join(p for p in parts if p)
    return rendered or None


def print_json_plan(loop_def: Any, spec: Any) -> None:
    """Print a validated JSON loop summary and its step tree."""
    console.print(f"[green]Valid:[/green] {spec.name} v{spec.version} ({spec.execution})")
    if spec.description:
        console.print(f"  {spec.description}")
    if spec.budget:
        rendered = _format_budget(spec.budget)
        if rendered:
            console.print("  budget: " + rendered)
    ep = spec.error_policy
    if ep is not None:
        fallback = f", fallback={ep.fallback_model}" if ep.fallback_model else ""
        console.print(
            f"  error_policy: retry={ep.retry}, on_failure={ep.on_failure.value}{fallback}"
        )
    console.print("[cyan]Steps:[/cyan]")
    _walk_spec_nodes(spec.steps, 1)
    console.print(f"[dim]source_hash: {loop_def.source_hash[:16]}…[/dim]")


def load_json_loop(loop_file: str) -> tuple[Any, Any]:
    """Load a JSON loop file, exiting with a friendly error on failure."""
    from loopmaster.spec.loader import load_loop_from_json_file

    try:
        return load_loop_from_json_file(loop_file)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Cannot read {loop_file}")
        raise typer.Exit(1) from None
    except json.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] invalid JSON in {loop_file}: {e}")
        raise typer.Exit(1) from None
