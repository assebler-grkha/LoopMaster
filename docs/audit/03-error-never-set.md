# Audit: LoopRunResult.error never set — CLI always shows success

**Severity:** P0 — Critical  
**File:** `core/engine.py`, `cli/app.py`  
**Lines:** engine.py:58, engine.py:373-376; cli/app.py:189  
**Tags:** usability, error-handling

## Problem

`LoopRunResult` has an `error: Exception | None` field (line 58), but `run()` never writes to it. The CLI (`cli/app.py:189`) checks `result.error` to display errors — it's always `None`. Users never see error information through the CLI.

## Impact

- Failed loops appear as successful in CLI output
- Users have no way to know their loop failed without reading logs
- Error handling in CLI is effectively dead code

## Root Cause

The engine wraps exceptions as `InterruptedError` (line 373-376) and raises them, but never stores them in `LoopRunResult.error`. The `run()` method's try/except doesn't capture the error into the result object.

## Fix Plan

### Step 1: Set error in LoopRunResult
In `LoopEngine.run()`, capture the error and set it on the result:

```python
async def run(self, ...) -> LoopRunResult:
    result = LoopRunResult(loop_id=self.loop_def.loop_id)
    try:
        # ... existing run logic
    except Exception as exc:
        result.error = exc  # ← ADD THIS
        result.status = "failed"  # ← ADD THIS
        raise  # Re-raise if needed, or handle
    finally:
        result.completed_at = datetime.now(timezone.utc)
    return result
```

### Step 2: Fix exception wrapping in _execute_step
Preserve original exception type instead of wrapping everything:

```python
except Exception as exc:
    # Instead of: raise InterruptedError(str(exc), step=step)
    # Wrap with context but preserve original:
    raise InterruptedError(
        str(exc), 
        step=step
    ) from exc  # ←链式保留原始异常
```

### Step 3: Update CLI error display
```python
# cli/app.py
if result.error:
    console.print(f"[red]Error: {result.error}[/red]")
    if hasattr(result.error, '__cause__') and result.error.__cause__:
        console.print(f"[dim]Caused by: {result.error.__cause__}[/dim]")
```

### Step 4: Add error to LoopRunResult if not present
Check if `LoopRunResult` dataclass has `error` field; if not, add it:
```python
@dataclass
class LoopRunResult:
    loop_id: str
    status: str = "pending"
    error: Exception | None = None  # ← ensure this exists
    # ...
```

### Tests:
- Run loop with failing step → verify `result.error` is set
- CLI displays error message for failed loops
- Original exception type is preserved in chain
