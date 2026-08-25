# ADR-011: JSON Config as Execution Format

**Status:** Proposed
**Supersedes (partially):** [ADR-001](001_python_dsl_as_source_of_truth.md)

## Context

ADR-001 made the Python DSL the source of truth after YAML proved inadequate for conditional branching. Since then, the MCP transport layer (ADR-006) turned loops into data consumed by agents over tool calls, and the roadmap added a loop marketplace and multi-agent orchestration. The next step is autonomous cycles defined as pure configuration:

- Agents must be able to *compose and launch* loops without writing `.py` files.
- Loops must live in a database, versioned and hash-pinned.
- Reusable executable code blocks must be stored in the DB and referenced from configs.
- Cycles must pause awaiting answers from an agent/human/MCP and continue later (durable HITL).

Python-as-source-of-truth conflicts with agent-authored, DB-stored, transport-friendly definitions.

## Decision

1. **LoopSpec (JSON)** becomes the *execution format*: a validated, schema'd declarative config (8 node types: llm, shell, http, mcp, code, human, parallel, conditional).
2. **JsonLoader compiles LoopSpec to the existing IR** (`Step`/`Parallel`/`Conditional`). The engine core is unchanged — runtime interpretation (ADR-002), data-only checkpoints (ADR-003) and replay semantics carry over as-is.
3. **The Python DSL remains an authoring format** with full backward compatibility; a compiler exports Python loops to LoopSpec (dual mode indefinitely). This supersedes ADR-001's "YAML export only" stance by replacing the export target.
4. Conditions inside JSON reuse the existing AST-whitelist expression parser — no new expression language, no eval().
5. Executable logic referenced from JSON lives in a Code Block Store (DB table, SemVer, SHA-256 pinning) and runs exclusively in subprocesses — never imported into the engine process.
6. Durable HITL: a `human` node persists a question message, checkpoints, frees the worker; answers arrive via MCP `loop_respond` and resume deterministically (branch stickiness preserved).

## Consequences

- (+) Agents can author, store, version and launch loops as data — no filesystem writes needed.
- (+) Engine core untouched: budget/error-policy/heartbeat/checkpoint machinery reused unchanged.
- (+) Marketplace-ready: loops and blocks are DB rows with hashes.
- (-) Two definition formats to maintain (mitigated: one-way compile Python→JSON; JSON never compiles back to Python).
- (-) JSON configs can grow into a "language in a language" (mitigated: hard v1 boundary — 8 node types, no user functions; all real logic in code blocks or whitelisted expressions).
- (-) DB-hosted code is an RCE surface if trusted blindly (mitigated: SHA-256 pinning, capabilities declaration, subprocess-only execution, deny-lists).
