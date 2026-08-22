# ADR-004: Custom Concurrency Supervisor

**Status:** Accepted

## Context

`asyncio.TaskGroup` was proposed for parallel step execution. Adversarial review identified that TaskGroup cancels ALL tasks on any exception — incompatible with ErrorPolicy retry/skip/fallback semantics.

## Decision

Use a custom concurrency supervisor. Options: `aiotools.PersistentTaskGroup`, `asyncio.gather(return_exceptions=True)`, or custom implementation.

## Consequences

- (+) ErrorPolicy works: retry/skip/fallback per step
- (+) Sibling steps survive individual failures
- (+) Full control over failure semantics
- (-) More code to write and maintain
- (-) Must handle task cancellation explicitly
