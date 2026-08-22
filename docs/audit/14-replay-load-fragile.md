# Audit: ReplaySession.load() fragile on extra keys

**Severity:** P3 — Low  
**File:** `core/replay.py`  
**Lines:** 56-66  
**Tags:** robustness

## Problem

`ReplaySession.load()` pops `recorded_steps` then passes `**data` to `cls()`. If JSON has unexpected keys, `cls()` raises TypeError (no extra kwargs handling).

## Impact

- Forward compatibility broken — old code can't load newer JSON formats
- Backward compatibility broken — newer code can't load older formats with missing keys

## Fix Plan

1. Filter known keys:
```python
@classmethod
def load(cls, filepath):
    with open(filepath) as f:
        data = json.load(f)
    recorded_steps = [RecordedStep(**s) for s in data.pop("recorded_steps", [])]
    # Only pass known keys
    known = {"session_id", "loop_id", "created_at", "recorded_steps"}
    filtered = {k: v for k, v in data.items() if k in known}
    return cls(recorded_steps=recorded_steps, **filtered)
```

2. Or use **kwargs with validation:
```python
def __init__(self, **kwargs):
    self.session_id = kwargs.pop("session_id", None)
    if kwargs:
        raise TypeError(f"Unknown kwargs: {kwargs.keys()}")
```

### Tests
- Load JSON with extra keys → no error
- Load JSON with missing keys → defaults used
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)
