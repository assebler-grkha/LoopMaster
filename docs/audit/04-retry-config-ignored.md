# Audit: Step retry config always overridden by error policy

**Severity:** P1 — High  
**File:** `core/engine.py`  
**Lines:** 401  
**Tags:** configuration, correctness

## Problem

```python
step_retries = max(step.retry, self.error_policy.retry)
```

- `Step.retry` default = 1 (core/types.py:164)
- `ErrorPolicy.retry` default = 2

`max(1, 2) = 2` — user's step-level retry config is always overridden. `Step(retry=5)` gets 2 retries. `Step(retry=0)` gets 2 retries.

## Impact

- User cannot customize retry behavior per-step
- `Step(retry=0)` (no retries) is impossible — always retries
- `Step(retry=10)` for critical steps silently ignored

## Fix Plan

1. Change `Step.retry` type to `int | None = None`
2. In engine: `step_retries = step.retry if step.retry is not None else self.error_policy.retry`
3. Update all Step() constructors to pass retry= explicitly where needed

### Tests
- `Step(retry=0)` → 0 retries
- `Step(retry=5)` → 5 retries
- `Step()` (None) → uses policy default
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)

**Commit:** 89504ad
