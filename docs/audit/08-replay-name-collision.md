# Audit: Replay name collision — only last recording survives

**Severity:** P2 — Medium  
**File:** `core/replay.py`  
**Lines:** 146-148  
**Tags:** correctness, data-loss

## Problem

```python
_results_by_name = {step.name: step for step in self.recorded_steps}
```

If two steps share the same name, only the last one's recording survives. Non-deterministic replay for loops with duplicate step names.

## Impact

- Duplicate step names cause silent data loss
- Replay skips steps unpredictably
- No warning or error for name collision

## Fix Plan

1. Use list-based lookup instead of dict:
```python
_results_by_index = {i: step for i, step in enumerate(self.recorded_steps)}
```

2. Or raise error on duplicate names:
```python
names = [s.name for s in self.recorded_steps]
if len(names) != len(set(names)):
    raise ValueError(f"Duplicate step names: {[n for n in names if names.count(n) > 1]}")
```

3. Or prefix with index: `f"{step.name}_{i}"`

### Tests
- Two steps with same name → error raised or index-based lookup
- Replay matches recorded steps correctly
