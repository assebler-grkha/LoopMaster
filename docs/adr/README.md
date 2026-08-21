# LoopMaster — Architecture Decision Records

## ADR-001: Python DSL as Source of Truth

**Status:** Accepted

**Context:** YAML was initially proposed for loop definitions. Adversarial review revealed YAML cannot express conditional branching without reinventing a template language. YAML + Python callbacks creates an impedance mismatch that grows worse with complexity.

**Decision:** Loops are defined in Python using decorators and runtime calls. YAML is an export format only (linear sequences in v1).

**Consequences:**
- (+) No YAML parser needed for authoring
- (+) Native Python control flow (if/else, for, while)
- (+) Checkpoint serialization works (data-only, no closures)
- (-) YAML portability is export-only, not bidirectional
- (-) Non-programmers cannot author loops (acceptable — target audience is developers)

## ADR-002: Runtime Interpretation

**Status:** Accepted

**Context:** Code generation from YAML was proposed. Adversarial review identified this as an architectural dead end: generated code is a black box, debugging is miserable, every schema change requires regeneration.

**Decision:** The engine interprets the loop definition at runtime. No intermediate code is generated.

**Execution model:** The runtime wraps the loop body in an executor. When the body calls `Step("name", ...)`, the runtime:
1. Freezes current context → immutable snapshot
2. Executes the step (LLM/tool call) — blocks until completion
3. Step returns a `StepOutput` (diff)
4. Runtime merges diff into context
5. Records step name in `executed_step_names`
6. Body continues — Python control flow evaluates naturally

Step() is lazy — executes immediately when called, not collected in advance. No two-phase execution. Dynamic control flow works because Python evaluates it at runtime.

**Consequences:**
- (+) No generated code to debug
- (+) Hot-reloading of loop definitions
- (+) Simpler architecture
- (+) Dynamic control flow (if/for/while) works naturally
- (-) Slightly slower startup (acceptable for loop execution timescales)

## ADR-003: Data-Only Checkpoints

**Status:** Accepted

**Context:** Checkpointing the full loop state (including Python closures) was proposed. Adversarial review identified this as impossible: closures close over modules, env, tool instances — not picklable.

**Decision:** Checkpoints store ONLY data: loop name, user semver, computed source hash, executed step names, context dict, step results, recorded responses. The loop definition is NEVER checkpointed. On resume, load loop from source by name+version, verify source hash, inject checkpoint data, re-execute body with skip-list.

**Consequences:**
- (+) Checkpoints are always valid (data-only, no code)
- (+) Version-bound with integrity check (source hash)
- (+) Dynamic control flow works (executed_step_names instead of step_index)
- (-) Requires loop registry (name → function mapping)
- (-) Context object must be serializable (plain dataclass, no connections)

## ADR-004: Custom Concurrency Supervisor

**Status:** Accepted

**Context:** `asyncio.TaskGroup` was proposed for parallel step execution. Adversarial review identified that TaskGroup cancels ALL tasks on any exception — incompatible with ErrorPolicy retry/skip/fallback semantics.

**Decision:** Use a custom concurrency supervisor. Options: `aiotools.PersistentTaskGroup`, `asyncio.gather(return_exceptions=True)`, or custom implementation.

**Consequences:**
- (+) ErrorPolicy works: retry/skip/fallback per step
- (+) Sibling steps survive individual failures
- (+) Full control over failure semantics
- (-) More code to write and maintain
- (-) Must handle task cancellation explicitly

## ADR-005: Immutable Context Passing

**Status:** Accepted

**Context:** Mutable context across parallel steps creates race conditions. Checkpointing mid-mutation captures inconsistent snapshots.

**Decision:** Each step receives an immutable snapshot of the context and returns a diff (StepOutput) that gets merged by the runtime.

**Consequences:**
- (+) Safe parallel execution
- (+) Deterministic checkpointing
- (+) Matches production patterns (LangGraph, OpenAI Agents SDK)
- (-) Slightly more memory (snapshots)
- (-) Steps cannot modify context directly (must return updates)

## ADR-006: MCP as Transport

**Status:** Accepted

**Context:** MCP was proposed as the full interaction facade. Adversarial review identified that MCP is request-response; loops run minutes and need events, streaming progress, and session continuity.

**Decision:** MCP is a thin transport layer. The Loop Protocol is its own contract for lifecycle management (discover, start, subscribe_events, pause, resume, cancel, get_status).

**Consequences:**
- (+) Standard tooling (MCP ecosystem)
- (+) Loop Protocol designed independently, not constrained by MCP
- (+) Events via OpenTelemetry spans + minimal LoopEvent schema
- (-) Two protocols to maintain (MCP transport + Loop Protocol)

## ADR-007: Cost Tracking as Middleware

**Status:** Accepted

**Context:** Cost tracking was proposed as core engine logic. Adversarial review identified that model-specific pricing is complex (tier, context position, thinking tokens) and should be separable from the core.

**Decision:** Cost tracking is a pluggable middleware that intercepts model calls. Budget enforcement via ErrorPolicy on BudgetExceeded exception.

**Consequences:**
- (+) Testable independently
- (+) Users can override pricing without modifying the library
- (+) Separation of concerns
- (-) Pricing tables need to be maintained (external to core)

## ADR-008: Agent Adapter Pattern

**Status:** Accepted

**Context:** Agent interaction was proposed as a monolithic "agent manager." Adversarial review identified that each agent has different config file locations, formats, and conventions.

**Decision:** AgentAdapter base class with concrete implementations per agent type. AgentRegistry for auto-discovery. ConfigManager for safe modification (snapshot → atomic write → verify → rollback).

**Consequences:**
- (+) Each agent type is isolated
- (+) Safe modification with rollback
- (+) Extensible (new agents = new adapter)
- (-) Must maintain adapters per agent type

## ADR-009: ErrorPolicy Objects

**Status:** Accepted

**Context:** Error handling was proposed as ad-hoc try/except in the loop body. Adversarial review identified that different errors need different recovery strategies.

**Decision:** Explicit ErrorPolicy objects that the loop consults on failure. Default policies provided, users can override per error type.

**Consequences:**
- (+) Error handling is explicit and configurable
- (+) Testable (mock ErrorPolicy in tests)
- (+) Default policies cover common cases
- (-) Users must learn the ErrorPolicy API

## ADR-010: Deterministic Replay

**Status:** Accepted

**Context:** Observability/tracing was proposed but no mechanism for deterministic replay. Debugging requires replaying with recorded responses, not re-calling the model.

**Decision:** Record all LLM responses and tool results. Replay mode injects recorded responses instead of calling the model.

**Consequences:**
- (+) Essential for debugging
- (+) Enables A/B testing of callbacks
- (+) Deterministic testing
- (-) Storage overhead for recorded responses
- (-) Replay mode must handle version mismatches
