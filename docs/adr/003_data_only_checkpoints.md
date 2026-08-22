# ADR-003: Data-Only Checkpoints

**Status:** Accepted

## Context

Checkpointing the full loop state (including Python closures) was proposed. Adversarial review identified this as impossible: closures close over modules, env, tool instances — not picklable.

## Decision

Checkpoints store ONLY data: loop name, user semver, computed source hash, executed step names, context dict, step results, recorded responses. The loop definition is NEVER checkpointed. On resume, load loop from source by name+version, verify source hash, inject checkpoint data, re-execute body with skip-list.

## Consequences

- (+) Checkpoints are always valid (data-only, no code)
- (+) Version-bound with integrity check (source hash)
- (+) Dynamic control flow works (executed_step_names instead of step_index)
- (-) Requires loop registry (name → function mapping)
- (-) Context object must be serializable (plain dataclass, no connections)
