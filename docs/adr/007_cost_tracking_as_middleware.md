# ADR-007: Cost Tracking as Middleware

**Status:** Accepted

## Context

Cost tracking was proposed as core engine logic. Adversarial review identified that model-specific pricing is complex (tier, context position, thinking tokens) and should be separable from the core.

## Decision

Cost tracking is a pluggable middleware that intercepts model calls. Budget enforcement via ErrorPolicy on BudgetExceeded exception.

## Consequences

- (+) Testable independently
- (+) Users can override pricing without modifying the library
- (+) Separation of concerns
- (-) Pricing tables need to be maintained (external to core)
