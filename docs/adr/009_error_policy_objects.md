# ADR-009: ErrorPolicy Objects

**Status:** Accepted

## Context

Error handling was proposed as ad-hoc try/except in the loop body. Adversarial review identified that different errors need different recovery strategies.

## Decision

Explicit ErrorPolicy objects that the loop consults on failure. Default policies provided, users can override per error type.

## Consequences

- (+) Error handling is explicit and configurable
- (+) Testable (mock ErrorPolicy in tests)
- (+) Default policies cover common cases
- (-) Users must learn the ErrorPolicy API
