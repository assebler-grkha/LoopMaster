"""LoopMaster CLI — thin wrapper around core library."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="loop-engine",
    help="LoopMaster — Design, validate, and run AI agent loops.",
    add_completion=False,
)
console = Console()


@app.command()
def init(
    name: str = typer.Argument(..., help="Loop name"),
    path: str = typer.Option(".", help="Directory to create the loop file"),
) -> None:
    """Initialize a new loop file from a template."""
    loop_file = Path(path) / f"{name}.py"
    if loop_file.exists():
        console.print(f"[red]Error:[/red] {loop_file} already exists")
        raise typer.Exit(1)

    template = f'''"""Loop: {name}"""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {name.replace("-", "_")}(ctx):
    Step("step_1", model="gpt-4", prompt="Hello from {name}")

    return ctx
'''

    loop_file.write_text(template, encoding="utf-8")
    console.print(f"[green]Created:[/green] {loop_file}")


@app.command()
def validate(
    loop_file: str = typer.Argument(
        ..., help="Path to loop Python file"
    ),
) -> None:
    """Validate a loop file without running it."""
    from loopmaster.core.engine import LoopEngine

    engine = LoopEngine()

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_loop_module", loop_file
        )
        if spec is None or spec.loader is None:
            console.print(f"[red]Error:[/red] Cannot load {loop_file}")
            raise typer.Exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        found = False
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
                engine.register(loop_def)
                console.print(
                    f"[green]Valid:[/green] "
                    f"{loop_def.name} v{loop_def.version}"
                )
                found = True

        if not found:
            console.print(
                "[yellow]Warning:[/yellow] No @Loop found in file"
            )
            raise typer.Exit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@app.command()
def run(
    loop_file: str = typer.Argument(
        ..., help="Path to loop Python file"
    ),
    resume: bool = typer.Option(
        False, "--resume", "-r", help="Resume from last checkpoint"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Validate without LLM calls"
    ),
) -> None:
    """Execute a loop."""
    from loopmaster.core.engine import LoopEngine

    engine = LoopEngine()

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_loop_module", loop_file
        )
        if spec is None or spec.loader is None:
            console.print(f"[red]Error:[/red] Cannot load {loop_file}")
            raise typer.Exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        loop_def = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
                break

        if loop_def is None:
            console.print("[red]Error:[/red] No @Loop found")
            raise typer.Exit(1)

        engine.register(loop_def)

        if dry_run:
            console.print(
                f"[cyan]Dry run:[/cyan] "
                f"{loop_def.name} v{loop_def.version}"
            )
            console.print("[cyan]No LLM calls will be made[/cyan]")
            result = engine.run(loop_def, {})
            if result.success:
                console.print("[green]Validation passed[/green]")
            else:
                console.print(
                    f"[red]Validation failed:[/red] {result.error}"
                )
                raise typer.Exit(1)
        else:
            checkpoint = None
            if resume:
                from loopmaster.checkpoint import CheckpointManager

                mgr = CheckpointManager()
                checkpoint = mgr.load_latest(loop_def.name)
                if checkpoint:
                    console.print(
                        f"[cyan]Resuming from checkpoint:[/cyan] "
                        f"steps {checkpoint.executed_step_names}"
                    )
                else:
                    console.print(
                        "[yellow]No checkpoint found, "
                        "starting fresh[/yellow]"
                    )

            console.print(
                f"[cyan]Running:[/cyan] "
                f"{loop_def.name} v{loop_def.version}"
            )
            result = engine.run(
                loop_def, {}, resume_checkpoint=checkpoint
            )

            if result.success:
                console.print(
                    "[green]Loop completed successfully[/green]"
                )
                console.print(
                    f"  Cost: ${result.total_cost:.4f}"
                )
                console.print(
                    f"  Tokens: {result.total_tokens}"
                )
                console.print(
                    f"  Steps: {', '.join(result.steps_executed)}"
                )
            else:
                console.print(
                    f"[red]Loop failed:[/red] {result.error}"
                )
                if result.checkpoint_saved:
                    console.print(
                        "[yellow]Checkpoint saved — "
                        "use --resume to continue[/yellow]"
                    )
                raise typer.Exit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@app.command()
def checkpoints(
    loop_name: str = typer.Argument(..., help="Loop name to check"),
) -> None:
    """List checkpoints for a loop."""
    from loopmaster.checkpoint import CheckpointManager

    mgr = CheckpointManager()
    items = mgr.list_checkpoints(loop_name)

    if not items:
        console.print(
            f"[yellow]No checkpoints found for {loop_name}[/yellow]"
        )
        return

    table = Table(title=f"Checkpoints: {loop_name}")
    table.add_column("Version")
    table.add_column("Steps")
    table.add_column("Created")

    for cp in items:
        table.add_row(
            cp.get("loop_version", "?"),
            str(len(cp.get("executed_step_names", []))),
            cp.get("created_at", "?"),
        )

    console.print(table)


@app.command()
def templates_list() -> None:
    """List available loop templates."""
    from loopmaster.templates import TEMPLATES

    table = Table(title="Available Templates")
    table.add_column("Name")
    table.add_column("Description")

    for tpl_name, desc in TEMPLATES.items():
        table.add_row(tpl_name, desc)

    console.print(table)


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
