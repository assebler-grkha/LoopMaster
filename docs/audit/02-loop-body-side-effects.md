# Audit: Loop body executes as side effect during step collection

**Severity:** P0 — Critical  
**File:** `core/engine.py`  
**Lines:** 212-229  
**Tags:** correctness, side-effects  
**Status:** FIXED

## Problem

`_collect_steps_from_loop()` calls `loop_def.body(ctx)` to discover `Step()` objects. This **executes the entire loop body** as a side effect. Any I/O, network calls, file writes, or API calls in the body happen during collection — before the engine actually runs.

During resume, the body executes again (collection phase + iteration phase = 3 total executions for a resumed loop).

## Impact

- Network requests, file writes, API calls happen during collection
- State mutations happen before engine tracking begins
- Resume causes triple execution of body side effects
- If body has external effects (sending emails, charging API), they happen multiple times

## Root Cause

The design requires calling `body(ctx)` to discover which `Step()` objects it creates. There's no separation between "declaration" and "execution" in the loop body.

## Fix Plan

### Option A: Declarative step registration (recommended)
Make `Step` objects register themselves at definition time, not at execution time.

```python
# In core/types.py — Step registers in a module-level registry
_step_registry: dict[str, list[Step]] = defaultdict(list)

@dataclass
class Step:
    name: str
    prompt: str
    # ...
    def __post_init__(self):
        loop_id = _current_loop_id.get()
        if loop_id:
            _step_registry[loop_id].append(self)
```

This eliminates the need to execute the body for collection.

### Option B: Generator-based body
Require loop bodies to be generators that yield Step objects:

```python
@Loop("my-loop")
def my_loop(ctx):
    yield Step("step1", prompt="...")
    yield Step("step2", prompt="...")
```

Engine collects via iteration without executing side effects.

### Option C: Two-phase body (minimal change)
Split body into declaration and execution phases:

```python
@Loop("my-loop")
def my_loop(ctx):
    # Declaration phase — no side effects
    steps = [
        Step("step1", prompt="..."),
        Step("step2", prompt="..."),
    ]
    return steps

# Engine calls body() once for collection, then executes steps
```

### Implementation steps (for Option A):
1. Add `_current_loop_id` thread-local and `_step_registry` dict to `core/types.py`
2. Modify `Step.__post_init__` to register itself
3. Modify `Loop()` decorator to set/clear `_current_loop_id`
4. Modify `LoopEngine._collect_steps_from_loop` to read from registry instead of calling body
5. Add cleanup after collection

### Tests needed:
- Verify body is NOT executed during collection
- Verify steps are correctly registered
- Verify cleanup after collection
- Verify resume doesn't re-execute body
