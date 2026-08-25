"""Block management commands (mirror of the MCP block_* tools)."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from loopmaster.mcp.job_store import get_job_store

app = typer.Typer(help="Manage reusable code blocks.")
console = Console()


@app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="Block name (kebab-case).")],
    version: Annotated[str, typer.Argument(help="Semantic version, e.g. 1.0.0.")],
    source: Annotated[Path, typer.Option("--source", "-s", help="Path to the source file.")],
    lang: Annotated[str, typer.Option("--lang", "-l", help="Language: python|shell.")] = "python",
    caps: Annotated[
        str, typer.Option("--caps", help="Comma-separated capabilities, e.g. net,fs:write:src/.")
    ] = "",
    description: Annotated[
        str, typer.Option("--description", "-d", help="Human-readable description.")
    ] = "",
) -> None:
    """Register a new immutable code block version."""
    try:
        src = source.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Cannot read source file:[/red] {exc}")
        raise typer.Exit(1) from exc
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]
    store = get_job_store()
    try:
        block = store.save_code_block(
            name=name,
            version=version,
            language=lang,
            source=src,
            capabilities=capabilities,
            description=description,
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Added[/green] {block.name}@{block.version} sha256={block.sha256[:16]}…")
    console.print(
        f'Pin it in a spec: "ref": "{block.name}@{block.version}", "sha256": "{block.sha256}"'
    )


@app.command("get")
def get(ref: Annotated[str, typer.Argument(help="Block ref: name or name@version.")]) -> None:
    """Show block metadata and source."""
    store = get_job_store()
    block = store.get_code_block(ref)
    if block is None:
        console.print(f"[red]Not found:[/red] {ref}")
        raise typer.Exit(1)
    payload = block.to_dict(include_source=True)
    try:
        payload["verified_sha256"] = hashlib.sha256(block.source.encode("utf-8")).hexdigest()
    except Exception:
        payload["verified_sha256"] = None
    console.print(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command("list")
def list_blocks(
    pattern: Annotated[str, typer.Argument(help="Optional name substring filter.")] = "",
) -> None:
    """List registered blocks (without sources)."""
    store = get_job_store()
    blocks = store.list_code_blocks(pattern=pattern or None)
    if not blocks:
        console.print(f"No blocks{' matching ' + pattern if pattern else ''}.")
        return
    for b in blocks:
        desc = f" — {b.description}" if b.description else ""
        console.print(f"{b.name}@{b.version} [{b.language}] sha256={b.sha256[:12]}…{desc}")


if __name__ == "__main__":
    app()
