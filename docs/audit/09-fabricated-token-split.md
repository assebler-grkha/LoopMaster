# Audit: Fabricated 70/30 token split

**Severity:** P2 — Medium  
**File:** `core/engine.py`  
**Lines:** 299  
**Tags:** correctness, metrics

## Problem

```python
input_tokens = int(result.tokens_used * 0.7)
```

Hardcoded 70/30 input/output split is fabricated data. No real API response parsing. Cost tracker receives inaccurate token counts.

## Impact

- Cost calculations are wrong (off by up to 30%)
- Metrics/reports show inaccurate token usage
- Users cannot trust cost data

## Fix Plan

1. Parse actual API response for token breakdown:
```python
# Assuming result has raw API response
input_tokens = result.raw_response.usage.prompt_tokens
output_tokens = result.raw_response.usage.completion_tokens
```

2. If API doesn't provide breakdown, report as unknown:
```python
input_tokens = None  # Unknown
output_tokens = None
```

3. Update CostTracker to handle None values

### Tests
- API with token breakdown → uses actual values
- API without breakdown → None, no cost calculation
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)
