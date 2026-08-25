"""Operational CLI commands: hooks listing, store maintenance, skill install."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def hooks_list() -> None:
    """List registered lifecycle hooks."""
    from loopmaster import hooks

    registry = hooks.get_registry()
    if not any(registry.values()):
        console.print("[yellow]No hooks registered[/yellow]")
        return

    table = Table(title="Registered Hooks")
    table.add_column("Event")
    table.add_column("Hooks")

    for event, names in sorted(registry.items()):
        if names:
            table.add_row(event, ", ".join(names))
    console.print(table)


def maintenance() -> None:
    """Run store maintenance: stale reaper + archive sweeper."""
    from loopmaster import hooks_builtin
    from loopmaster.mcp.job_store import get_job_store

    store = get_job_store()
    reaped = hooks_builtin.h_stale_reaper(store)
    swept = hooks_builtin.h_archive_sweeper(store)
    console.print(f"[green]Interrupted stale jobs:[/green] {reaped}")
    console.print(f"[green]Swept notifications archived:[/green] {swept}")


def skills(
    install: bool = typer.Option(False, "--install", help="Install skills into target directory"),
    target: str = typer.Option(".opencode/skill", "--target", help="Installation target dir"),
) -> None:
    """List or install bundled agent skills (S1-S9)."""
    skills_dir = Path(__file__).resolve().parent.parent.parent.parent / "skills"
    if not skills_dir.exists():
        console.print("[red]Error:[/red] skills directory not found")
        raise typer.Exit(1)

    packs = sorted(p for p in skills_dir.iterdir() if (p / "SKILL.md").exists())
    if not install:
        for p in packs:
            console.print(f"[cyan]{p.name}[/cyan]")
        return

    target_dir = Path(target)
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = 0
    for p in packs:
        dest = target_dir / p.name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(
            (p / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
        installed += 1
    console.print(f"[green]Installed {installed} skill(s) into {target_dir}[/green]")
