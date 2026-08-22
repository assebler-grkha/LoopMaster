# Audit: Metrics load() restores points but not loops

**Severity:** P3 — Low  
**File:** `metrics/collector.py`  
**Tags:** correctness, data-loss

## Problem

`MetricsCollector.load()` only restores `MetricPoint` objects, NOT `LoopMetrics` metadata. After load, loop-level aggregations are lost.

## Impact

- Loop metrics (avg latency, success rate, etc.) are lost after restart
- Reports show incomplete data

## Fix Plan

1. Save and restore loop metadata:
```python
def save(self, filepath):
    data = {
        "points": [asdict(p) for p in self._points],
        "loops": {k: asdict(v) for k, v in self._loops.items()}
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

def load(self, filepath):
    with open(filepath) as f:
        data = json.load(f)
    self._points = [MetricPoint(**p) for p in data.get("points", [])]
    self._loops = {k: LoopMetrics(**v) for k, v in data.get("loops", {}).items()}
```

2. Or recompute loops from points on load:
```python
def load(self, filepath):
    # Load points only
    # Then: self._recompute_loops()
```

### Tests
- Save/load preserves loop metadata
- Recompute from points gives same result
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)
