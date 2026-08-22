# Audit: Snapshot never populated — restore is no-op

**Severity:** P0 — Critical  
**File:** `agents/adapter.py`  
**Lines:** 98, 173, 227-239, 244-247, 268  
**Tags:** data-loss, correctness  
**Status:** FIXED

## Problem

All adapters (`CustomAdapter`, `OpenCodeAdapter`, `ClaudeCodeAdapter`, `CursorAdapter`) initialize `_snapshots: dict[Path, bytes] = {}` but **never call `snapshot()`** before writing to config files.

`inject_loop_context()` and `write_config()` modify user config files (e.g., `~/.config/opencode/agents/*.md`, `~/.cursor/rules`) directly. `restore_original()` iterates `_snapshots` which is always empty → **restore is a no-op**. Original config is permanently overwritten.

## Impact

- User's agent configuration files are modified without backup
- `restore_original()` gives false sense of safety
- Running LoopMaster corrupts global agent configs irreversibly

## Root Cause

The `snapshot()` method exists on all adapters but is never called in the workflow. The `inject_loop_context()` method should call `self.snapshot(path)` before writing.

## Fix Plan

### Step 1: Call snapshot before every write
In each adapter's `inject_loop_context()` and `write_config()`, call `self.snapshot(target_path)` before modifying the file.

**Example fix for `OpenCodeAdapter.inject_loop_context`:**
```python
def inject_loop_context(self, loop_context: dict, paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            self.snapshot(path)  # ← ADD THIS
            original = path.read_text(encoding="utf-8")
            # ... rest of injection logic
```

### Step 2: Apply same pattern to all adapters
- `CustomAdapter.write_config()` — snapshot before write
- `ClaudeCodeAdapter.inject_loop_context()` — snapshot before write  
- `CursorAdapter.inject_loop_context()` — snapshot before write

### Step 3: Add safety check in restore_original
```python
def restore_original(self) -> None:
    if not self._snapshots:
        # Log warning: no snapshots were taken
        return
    for path, content in self._snapshots.items():
        path.write_bytes(content)
```

### Step 4: Add tests
- Test that `_snapshots` is populated after `inject_loop_context()`
- Test that `restore_original()` actually restores file content
- Test round-trip: inject → verify modified → restore → verify original

## Verification

1. Run existing tests: `pytest tests/`
2. Manual test: run `loop-engine init` then check agent config files are backed up
3. Verify `restore_original()` restores original content
