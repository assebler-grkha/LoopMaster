# ADR-005: Immutable Context Passing

**Status:** Accepted

## Context

Mutable context across parallel steps creates race conditions. Checkpointing mid-mutation captures inconsistent snapshots.

## Decision

Each step receives an immutable snapshot of the context and returns a diff (StepOutput) that gets merged by the runtime.

## Consequences

- (+) Safe parallel execution
- (+) Deterministic checkpointing
- (+) Matches production patterns (LangGraph, OpenAI Agents SDK)
- (-) Slightly more memory (snapshots)
- (-) Steps cannot modify context directly (must return updates)
