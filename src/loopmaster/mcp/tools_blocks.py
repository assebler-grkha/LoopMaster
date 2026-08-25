"""Code-block MCP tools and spec reference validation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.runtime import mcp


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


def validate_code_refs(data: dict[str, Any]) -> str | None:
    """Check that referenced code blocks exist and sha256 pins match."""
    deny = data.get("deny_capabilities") or []
    for node in _iter_code_nodes(data.get("steps") or []):
        ref = node.get("ref", "")
        block = rt.store.get_code_block(ref)
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
        block = rt.store.save_code_block(
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
    block = rt.store.get_code_block(ref)
    if block is None:
        return json.dumps({"error": f"code block '{ref}' not found"})
    return json.dumps(
        {**block.to_dict(), "verified_sha256": rt.store.verify_code_block(ref)}, indent=2
    )


@mcp.tool()
def block_list(pattern: str | None = None) -> str:
    """List registered code blocks (metadata only, no source)."""
    blocks = rt.store.list_code_blocks(pattern=pattern)
    return json.dumps(
        {
            "count": len(blocks),
            "blocks": [b.to_dict(include_source=False) for b in blocks],
        },
        indent=2,
    )
