# Skill: block-manager

---
name: block-manager
description: Register, inspect, and verify reusable code blocks stored in the database.
---

Manage code blocks for `type: "code"` loop steps.

## Steps

1. Author a block: reads one JSON object from stdin (`{"input": ..., "context": {...}}`), writes one JSON object to stdout (`{"ok": true, "output": {...}, "logs": [...]}`). See `docs/BLOCKS.md`.
2. Register: `block_add(name="my-block", version="1.0.0", language="python", source=<code>, capabilities="net", description="...")` — note the returned sha256.
3. Inspect: `block_get(ref="my-block@1.0.0")` (full source + verified hash) or `block_list(pattern=...)`.
4. Reference in a spec: `{"type": "code", "name": "step", "ref": "my-block@1.0.0", "sha256": "<pin>", "input": {"k": "{ctx_var}"}}`.
5. Blocks are immutable — changes require a new version.
6. Capabilities (`net`, `fs:read:<prefix>`, `fs:write:<prefix>`) can be restricted per-spec via top-level `deny_capabilities`.
