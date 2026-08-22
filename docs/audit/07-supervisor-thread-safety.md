# Audit: Supervisor thread-safety race condition

**Severity:** P2 — Medium  
**File:** `core/supervisor.py`  
**Lines:** 56-59  
**Tags:** concurrency, correctness

## Problem

`result.results[step.name] = step_result` written from concurrent async tasks in thread pool. While dict assignment is GIL-atomic in CPython, the compound check-then-act on `all_succeeded` and `errors.append()` is a race condition:

```python
result.results[step.name] = step_result
if not step_result.success:
    result.all_succeeded = False  # ← read-modify-write
    result.errors.append(step_result)  # ← append is atomic, but ordering not guaranteed
```

Two threads can both read `all_succeeded = True`, both see failure, both write `False` — this specific case is fine, but `errors` list ordering is non-deterministic.

## Impact

- Error list ordering is non-deterministic (cosmetic issue)
- In theory, `all_succeeded` could be overwritten by a concurrent read (unlikely under CPython GIL but not guaranteed by spec)

## Fix Plan

1. Use `threading.Lock` for result aggregation:
```python
with self._result_lock:
    result.results[step.name] = step_result
    if not step_result.success:
        result.all_succeeded = False
        result.errors.append(step_result)
```

2. Or collect results after gather completes:
```python
results = await asyncio.gather(*tasks, return_exceptions=True)
for step, res in zip(steps, results):
    # Aggregate in single thread
```

### Tests
- Concurrent step execution → results dict consistent
- Error ordering deterministic
