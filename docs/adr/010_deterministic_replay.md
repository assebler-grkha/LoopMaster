# ADR-010: Deterministic Replay

**Status:** Accepted

## Context

Observability/tracing was proposed but no mechanism for deterministic replay. Debugging requires replaying with recorded responses, not re-calling the model.

## Decision

Record all LLM responses and tool results. Replay mode injects recorded responses instead of calling the model.

## Consequences

- (+) Essential for debugging
- (+) Enables A/B testing of callbacks
- (+) Deterministic testing
- (-) Storage overhead for recorded responses
- (-) Replay mode must handle version mismatches
