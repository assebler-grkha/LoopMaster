# Audit: MCP server stubs — no actual execution

**Severity:** P3 — Low  
**File:** `mcp/__init__.py`  
**Lines:** start_loop, pause_loop, resume_loop  
**Tags:** stubs, incomplete

## Problem

`start_loop`, `pause_loop`, `resume_loop` are stubs — no actual execution or checkpoint logic. They create/modify job status strings but don't run loops.

## Impact

- MCP integration is non-functional
- Users calling MCP tools get no results
- False advertising in feature list

## Fix Plan

1. Implement actual execution in `start_loop`:
```python
async def start_loop(self, loop_id: str, params: dict):
    engine = LoopEngine(...)
    result = await engine.run(...)
    return result
```

2. Or clearly mark as TODO/not-implemented:
```python
async def start_loop(self, loop_id: str, params: dict):
    raise NotImplementedError("MCP loop execution not yet implemented")
```

3. Remove from public API if not intended for use

### Tests
- start_loop raises NotImplementedError or executes correctly
