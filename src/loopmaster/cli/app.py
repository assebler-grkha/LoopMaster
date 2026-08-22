"""LoopMaster CLI — thin wrapper around core library."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
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
    template: str | None = typer.Option(
        None, "--template", "-t", help="Template to use (reflection, tool_use, planning, etc.)"
    ),
    task: str = typer.Option("...", help="Task description for the template"),
) -> None:
    """Initialize a new loop file from a template."""
    from loopmaster.templates import TEMPLATES, generate_code

    loop_file = Path(path) / f"{name}.py"
    if loop_file.exists():
        console.print(f"[red]Error:[/red] {loop_file} already exists")
        raise typer.Exit(1)

    if template:
        if template not in TEMPLATES:
            available = ", ".join(TEMPLATES.keys())
            console.print(
                f"[red]Error:[/red] Unknown template '{template}'. Available: {available}"
            )
            raise typer.Exit(1)
        code = generate_code(template, name_var=name, task=task)
    else:
        func_name = name.replace("-", "_")
        code = f'''"""Loop: {name}"""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("step_1", model="gpt-4", prompt="Hello from {name}")

    return ctx
'''

    loop_file.write_text(code, encoding="utf-8")
    console.print(f"[green]Created:[/green] {loop_file}")


@app.command()
def validate(
    loop_file: str = typer.Argument(..., help="Path to loop Python file"),
) -> None:
    """Validate a loop file without running it."""
    from loopmaster.core.engine import LoopEngine
    from loopmaster.core.types import LoopDef

    engine = LoopEngine()

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_loop_module", loop_file)
        if spec is None or spec.loader is None:
            console.print(f"[red]Error:[/red] Cannot load {loop_file}")
            raise typer.Exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        found = False
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, LoopDef):
                engine.register(attr)
                console.print(f"[green]Valid:[/green] {attr.name} v{attr.version}")
                found = True
            elif hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
                engine.register(loop_def)
                console.print(f"[green]Valid:[/green] {loop_def.name} v{loop_def.version}")
                found = True

        if not found:
            console.print("[yellow]Warning:[/yellow] No @Loop found in file")
            raise typer.Exit(1)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@app.command()
def run(
    loop_file: str = typer.Argument(..., help="Path to loop Python file"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume from last checkpoint"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Validate without LLM calls"),
) -> None:
    """Execute a loop."""
    from loopmaster.core.engine import LoopEngine

    engine = LoopEngine()

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_loop_module", loop_file)
        if spec is None or spec.loader is None:
            console.print(f"[red]Error:[/red] Cannot load {loop_file}")
            raise typer.Exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from loopmaster.core.types import LoopDef as LoopDefType

        loop_def = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, LoopDefType):
                loop_def = attr
                break
            elif hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
                break

        if loop_def is None:
            console.print("[red]Error:[/red] No @Loop found")
            raise typer.Exit(1)

        engine.register(loop_def)

        if dry_run:
            console.print(f"[cyan]Dry run:[/cyan] {loop_def.name} v{loop_def.version}")
            console.print("[cyan]No LLM calls will be made[/cyan]")
            result = engine.run(loop_def, {})
            if result.success:
                console.print("[green]Validation passed[/green]")
            else:
                console.print(f"[red]Validation failed:[/red] {result.error}")
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
                    console.print("[yellow]No checkpoint found, starting fresh[/yellow]")

            console.print(f"[cyan]Running:[/cyan] {loop_def.name} v{loop_def.version}")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Executing loop...", total=None)
                result = engine.run(loop_def, {}, resume_checkpoint=checkpoint)
                progress.update(task, completed=True, description="Done")

            if result.success:
                console.print("[green]Loop completed successfully[/green]")
                console.print(f"  Cost: ${result.total_cost:.4f}")
                console.print(f"  Tokens: {result.total_tokens}")
                console.print(f"  Steps: {', '.join(result.steps_executed)}")
            else:
                console.print(f"[red]Loop failed:[/red] {result.error}")
                if result.checkpoint_saved:
                    console.print("[yellow]Checkpoint saved — use --resume to continue[/yellow]")
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
        console.print(f"[yellow]No checkpoints found for {loop_name}[/yellow]")
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


@app.command(name="templates")
def templates_list() -> None:
    """List available loop templates."""
    from loopmaster.templates import list_templates

    table = Table(title="Available Templates")
    table.add_column("Name")
    table.add_column("Description")

    for tpl_name, desc in list_templates().items():
        table.add_row(tpl_name, desc)

    console.print(table)


@app.command()
def export(
    loop_file: str = typer.Argument(..., help="Path to loop Python file"),
    output: str | None = typer.Option(None, "-o", "--output", help="Output YAML file path"),
) -> None:
    """Export a loop definition as YAML."""
    from loopmaster.core.types import LoopDef as LoopDefType
    from loopmaster.core.yaml_export import export_loop

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_loop_module", loop_file)
        if spec is None or spec.loader is None:
            console.print(f"[red]Error:[/red] Cannot load {loop_file}")
            raise typer.Exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        loop_def = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, LoopDefType):
                loop_def = attr
                break
            elif hasattr(attr, "_loop_def"):
                loop_def = attr._loop_def
                break

        if loop_def is None:
            console.print("[red]Error:[/red] No @Loop found in file")
            raise typer.Exit(1)

        yaml_str = export_loop(loop_def)

        if output:
            from pathlib import Path

            Path(output).write_text(yaml_str, encoding="utf-8")
            console.print(f"[green]Exported:[/green] {output}")
        else:
            console.print(yaml_str)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@app.command()
def docs(
    open_browser: bool = typer.Option(True, help="Open docs in browser"),
) -> None:
    """Open or generate project documentation."""
    from pathlib import Path

    docs_dir = Path(__file__).resolve().parent.parent.parent.parent / "docs"

    if not docs_dir.exists():
        console.print("[red]Error:[/red] docs directory not found")
        raise typer.Exit(1)

    adr_dir = docs_dir / "adr"
    adr_files = sorted(adr_dir.glob("*.md")) if adr_dir.exists() else []

    table = Table(title="Documentation")
    table.add_column("File")
    table.add_column("Description")

    readme = docs_dir / "README.md"
    if readme.exists():
        table.add_row("README.md", "Project overview")

    for f in adr_files:
        if f.name == "README.md":
            continue
        table.add_row(f"adr/{f.name}", f.stem.replace("_", " ").title())

    console.print(table)
    console.print(f"\nDocs directory: [cyan]{docs_dir}[/cyan]")

    if open_browser:
        import os
        import platform
        import subprocess

        try:
            if platform.system() == "Windows":
                os.startfile(str(docs_dir))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(docs_dir)], check=False)  # noqa: S603
            else:
                subprocess.run(["xdg-open", str(docs_dir)], check=False)  # noqa: S603
            console.print("[green]Opened docs directory[/green]")
        except Exception:
            console.print("[yellow]Could not open browser automatically[/yellow]")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
