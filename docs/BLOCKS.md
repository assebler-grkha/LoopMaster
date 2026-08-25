# Block Authoring Guide

Code blocks are reusable, versioned pieces of Python or shell code stored in the
LoopMaster database. A loop references them by name and version; the engine runs
them in an isolated subprocess and merges their output back into the loop context.

## 1. The contract

Every block is executed as a **separate process**:

- **stdin** receives one JSON document: `{"input": <resolved input>, "context": <full loop context>}`
- **stdout** must be a single JSON document on the last line:
  `{"ok": true, "output": {...}, "logs": [...]}`
- `output` is merged into the step's result and becomes available to later steps
  via `{step-name.output.<key>}` templates.
- Non-zero exit code or `"ok": false` marks the step as failed (handled by the
  loop's error policy).

### Python block

```python
import json
import sys

payload = json.load(sys.stdin)
n = payload["input"]["n"]

print(json.dumps({
    "ok": True,
    "output": {"doubled": n * 2},
    "logs": [f"doubled {n}"],
}))
```

### Shell block

Shell blocks follow the same contract using any tooling available:

```bash
#!/usr/bin/env bash
# stdin carries the JSON payload; parse it with jq/python as needed
n=$(python -c "import json,sys; print(json.load(sys.stdin)['input']['n'])")
python -c "import json; print(json.dumps({'ok': True, 'output': {'doubled': $n * 2}, 'logs': []}))"
```

## 2. Registration

Blocks are immutable once registered under `(name, version)` — a change means
registering a new version. The store pins each block to the SHA-256 of its source.

```bash
loop-engine block add NAME VERSION --lang python --source path/to/block.py \
    --caps net,"fs:read:data/" --description "Doubles a number"
```

Or via MCP:

```
block_add(name, version, language, source, capabilities, description)
```

Both return the computed SHA-256. Reference that hash in a spec node to pin the
exact revision you reviewed.

## 3. Referencing from a LoopSpec

```json
{
  "type": "code",
  "name": "double-it",
  "ref": "mcp-doubler@1.0.0",
  "sha256": "84f72c7965b1e47e...",
  "input": { "n": "{n}" },
  "timeout": 30
}
```

- `ref` (required) — `name@semver`.
- `sha256` (optional but recommended) — fails fast at run time if the stored
  source no longer matches.
- `input` (optional) — values support `{placeholder}` templates resolved from
  the loop context.
- `timeout` (optional, default 60 s).

Before running loops with `code` nodes, validate references exist and hashes
match (`validate_code_refs` runs automatically in `loop_save` / `loop_run`).

## 4. Sandbox guarantees

- **Subprocess only** — nothing is imported or executed inside the engine process.
- Extraction cache at `%TEMP%/loopmaster-blocks/<sha256>/`; contents are re-verified
  against the digest before reuse.
- **Minimal environment**: only `PATH`, encoding vars, OS essentials plus anything
  explicitly allowed via the executor's `env_allow`. Pass secrets through `input`,
  not the environment.
- **Output limit**: stdout is drained with a hard cap of 1 MiB; exceeding it fails
  the step.
- **Timeout** (default 60 s) kills the whole process tree.
- **Capabilities** declared per block (`net`, `fs:read:<prefix>`, `fs:write:<prefix>`)
  are informational in v1 but enforced against the spec's top-level
  `deny_capabilities` list at load time and again at execution time.

## 5. Checklist for a good block

1. Read exactly one JSON object from stdin; never prompt interactively.
2. Print exactly one JSON object to stdout; send diagnostics to `logs` or stderr.
3. Return `"ok": false` (plus an `error` field in `output`) for expected business
   failures instead of non-zero exits, so callers can branch on results.
4. Keep blocks small and single-purpose; compose workflows in specs.
5. Write a clear `description` — agents discover blocks via `block_list` /
   `block_get` and rely on it.
