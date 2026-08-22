# Audit: Resume count accumulates across run() calls

**Severity:** P1 — High  
**File:** `core/engine.py`  
**Lines:** 194-210  
**Tags:** correctness, state-management

## Problem

`_init_run_state` increments `self._resume_count += 1` (line 206) on the Engine instance. When Engine is reused across multiple `run()` calls, resume_count keeps incrementing even on fresh runs. After N runs, `_resume_count = N` even if no actual resumes occurred.

## Impact

- Resume count is inaccurate after engine reuse
- Checkpoint logic may behave incorrectly with stale count
- Metrics/reporting show wrong resume counts

## Fix Plan

1. Reset `_resume_count = 0` at start of `run()` when `checkpointresume=False`
2. Or track resume count per-run, not per-engine instance

```python
async def run(self, ..., checkpoint_resume=False):
    self._resume_count = 0  # Reset per run
    if checkpoint_resume:
        self._resume_count = self._load_resume_count()
```

### Tests
- Two sequential run() calls → resume_count resets
- checkpoint_resume=True → loads from checkpoint
