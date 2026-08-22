# ADR-001: Python DSL as Source of Truth

**Status:** Accepted

## Context

YAML was initially proposed for loop definitions. Adversarial review revealed YAML cannot express conditional branching without reinventing a template language. YAML + Python callbacks creates an impedance mismatch that grows worse with complexity.

## Decision

Loops are defined in Python using decorators and runtime calls. YAML is an export format only (linear sequences in v1).

## Consequences

- (+) No YAML parser needed for authoring
- (+) Native Python control flow (if/else, for, while)
- (+) Checkpoint serialization works (data-only, no closures)
- (-) YAML portability is export-only, not bidirectional
- (-) Non-programmers cannot author loops (acceptable — target audience is developers)
