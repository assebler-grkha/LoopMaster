# ADR-006: MCP as Transport

**Status:** Accepted

## Context

MCP was proposed as the full interaction facade. Adversarial review identified that MCP is request-response; loops run minutes and need events, streaming progress, and session continuity.

## Decision

MCP is a thin transport layer. The Loop Protocol is its own contract for lifecycle management (discover, start, subscribe_events, pause, resume, cancel, get_status).

## Consequences

- (+) Standard tooling (MCP ecosystem)
- (+) Loop Protocol designed independently, not constrained by MCP
- (+) Events via OpenTelemetry spans + minimal LoopEvent schema
- (-) Two protocols to maintain (MCP transport + Loop Protocol)
