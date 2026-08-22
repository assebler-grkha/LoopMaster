# ADR-002: Runtime Interpretation

**Status:** Accepted

## Context

Code generation from YAML was proposed. Adversarial review identified this as an architectural dead end: generated code is a black box, debugging is miserable, every schema change requires regeneration.

## Decision

The engine interprets the loop definition at runtime. No intermediate code is generated.

### Execution Model

The runtime wraps the loop body in an executor. When the body calls `Step("name", ...)`, the runtime:

1. Freezes current context → immutable snapshot
2. Executes the step (LLM/tool call) — blocks until completion
3. Step returns a `StepOutput` (diff)
4. Runtime merges diff into context
5. Records step name in `executed_step_names`
6. Body continues — Python control flow evaluates naturally

Step() is lazy — executes immediately when called, not collected in advance. No two-phase execution. Dynamic control flow works because Python evaluates it at runtime.

## Consequences

- (+) No generated code to debug
- (+) Hot-reloading of loop definitions
- (+) Simpler architecture
- (+) Dynamic control flow (if/for/while) works naturally
- (-) Slightly slower startup (acceptable for loop execution timescales)
