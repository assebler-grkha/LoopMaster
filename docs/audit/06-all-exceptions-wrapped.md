# Audit: All exceptions wrapped as InterruptedError

**Severity:** P1 — High  
**File:** `core/engine.py`  
**Lines:** 373-376  
**Tags:** error-handling, debugging

## Problem

```python
except Exception as exc:
    raise InterruptedError(str(exc), step=step)
```

Catches ALL exceptions and wraps as `InterruptedError`, losing original type. `ValueError`, `FileNotFoundError`, `PermissionError` all become `InterruptedError`. `ErrorPolicy.classify()` never sees original types — recovery actions are always based on `InterruptedError`, not the real error.

## Impact

- Error policy cannot distinguish error types
- Recovery actions are always generic (retry/skip) instead of specific
- Debugging is harder — stack trace shows InterruptedError, not root cause
- Original exception context lost (unless `from exc` is used)

## Fix Plan

1. Wrap with `from exc` to preserve chain:
```python
except Exception as exc:
    raise InterruptedError(str(exc), step=step) from exc
```

2. Better: use specific exception types for wrapping, or don't wrap at all — let original propagate with step context added:
```python
except Exception as exc:
    exc.step = step  # Attach context
    raise
```

3. Update ErrorPolicy.classify() to check `__cause__` chain:
```python
def classify(self, error):
    cause = getattr(error, '__cause__', None)
    actual = cause if cause else error
    # Classify based on actual type
```

### Tests
- ValueError in step → policy sees ValueError, not InterruptedError
- Original exception preserved in chain
- Step context available on original exception
