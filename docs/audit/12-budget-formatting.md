# Audit: BudgetExceededError formats non-dollar amounts as dollars

**Severity:** P3 — Low  
**File:** `core/exceptions.py`  
**Tags:** usability, formatting

## Problem

`BudgetExceededError.__init__` formats as `$spent / $limit` even for step-count budgets. Non-dollar amounts display dollar formatting.

## Impact

- Confusing output: "Budget exceeded: $5 / $3" for step counts
- Misleading metrics in logs

## Fix Plan

1. Accept budget type parameter:
```python
class BudgetExceededError(LoopError):
    def __init__(self, spent, limit, budget_type="currency"):
        if budget_type == "currency":
            msg = f"Budget exceeded: ${spent:.2f} / ${limit:.2f}"
        else:
            msg = f"Budget exceeded: {spent} / {limit}"
```

2. Or use Budget object's type info to format correctly

### Tests
- Currency budget → dollar formatting
- Step budget → plain number formatting
