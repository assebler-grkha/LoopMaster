# LoopMaster — Architecture

## High-Level Architecture

```
┌─────────────────────────────────────────────────┐
│                  User / Agent                     │
│         (writes Python DSL, runs CLI)            │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                  CLI Layer                        │
│     init · validate · run · templates · docs     │
│         (thin wrapper around library)            │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│               Core Engine                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ Runtime  │ │ Checkpoint│ │ Error Recovery   │ │
│  │ Interp.  │ │ Manager   │ │ (ErrorPolicy)    │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ Cost     │ │ Metrics  │ │ Interruption     │ │
│  │ Tracker  │ │ Collector│ │ Protection       │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌──────────────────────────────────────────────┐│
│  │        Concurrency Supervisor                ││
│  │    (custom, NOT asyncio.TaskGroup)           ││
│  └──────────────────────────────────────────────┘│
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              Agent Interaction Layer              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ Agent    │ │ Config   │ │ Prompt           │ │
│  │ Registry │ │ Manager  │ │ Manager          │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌──────────────────────────────────────────────┐│
│  │         AgentAdapter (base class)            ││
│  │  OpenCode · ClaudeCode · Cursor · Custom     ││
│  └──────────────────────────────────────────────┘│
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│               Integration Layer                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ MCP      │ │ Loop     │ │ Event            │ │
│  │ Transport│ │ Protocol │ │ Emitter (OTel)   │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────┘
```

## Package Structure

```
loopmaster/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── engine.py          # Main loop runtime interpreter
│   ├── types.py           # Loop, Step, Parallel, StepResult, LoopState
│   ├── context.py         # Immutable context passing between steps
│   ├── supervisor.py      # Custom concurrency supervisor (NOT TaskGroup)
│   └── exceptions.py      # LoopError, StepError, CheckpointError, BudgetExceeded
├── checkpoint/
│   ├── __init__.py
│   ├── manager.py         # Checkpoint creation, loading, versioning, source hash verification
│   └── models.py          # Checkpoint data model (data-only: name, semver, source_hash, executed_step_names)
├── recovery/
│   ├── __init__.py
│   ├── error_policy.py    # ErrorPolicy objects for error recovery
│   └── interruption.py    # Interruption protection (heartbeat, detection, resume)
├── cost/
│   ├── __init__.py
│   ├── tracker.py         # Cost tracking middleware
│   ├── models.py          # CostRecord, Budget
│   └── pricing.py         # Provider-specific pricing tables
├── metrics/
│   ├── __init__.py
│   ├── collector.py       # MetricsCollector (in-memory + disk + external)
│   ├── models.py          # Metric definitions
│   └── exporters.py       # SQLite, OTel, Prometheus exporters
├── agents/
│   ├── __init__.py
│   ├── adapter.py         # AgentAdapter base class
│   ├── registry.py        # AgentRegistry (auto-discovery)
│   ├── config_manager.py  # Safe config modification (snapshot + rollback)
│   ├── prompt_manager.py  # System prompt injection (HTML comment markers)
│   ├── opencode.py        # OpenCodeAdapter
│   ├── claude_code.py     # ClaudeCodeAdapter
│   └── cursor.py          # CursorAdapter
├── mcp/
│   ├── __init__.py
│   ├── transport.py       # MCP server (thin transport layer)
│   └── loop_protocol.py   # Loop lifecycle protocol (discover, start, events, pause, resume)
├── events/
│   ├── __init__.py
│   ├── emitter.py         # EventEmitter (OTel + LoopEvent)
│   └── models.py          # LoopEvent schema
├── cli/
│   ├── __init__.py
│   ├── app.py             # Typer CLI app
│   ├── commands/
│   │   ├── init.py
│   │   ├── validate.py
│   │   ├── run.py
│   │   ├── templates.py
│   │   └── docs.py
│   └── progress.py        # Rich progress bars
├── templates/
│   ├── reflection.py
│   ├── tool_use.py
│   ├── planning.py
│   ├── multi_agent.py
│   ├── critique.py
│   ├── escalation.py
│   └── hybrid.py
└── utils/
    ├── __init__.py
    ├── llm.py             # LLM provider abstraction (v2: full abstraction; v1: Callable)
    └── serialization.py   # JSON serialization helpers
```

## Key Architectural Decisions

### 1. Runtime Execution Model

The engine wraps the loop body in an executor. When the body calls `Step("name", ...)`:

1. Runtime freezes the current context → immutable snapshot
2. Snapshot passed to the step
3. Step executes (LLM call / tool call) — blocks until completion
4. Step returns a `StepOutput` (diff of updates)
5. Runtime merges diff into the context
6. Runtime records step name in `executed_step_names`
7. Body continues — Python control flow (if/for/while) evaluates normally

**Key properties:**
- Step() is lazy — executes immediately when called, not collected in advance
- No two-phase execution — the body runs once, naturally
- Dynamic control flow works because Python evaluates it at runtime
- Checkpointing uses `executed_step_names` (not step_index) for branch-independent resume

### 2. Python DSL as Source of Truth (NOT YAML)

**Decision:** Loops are defined in Python using decorators and runtime calls. YAML is an export format only.

**Rationale:**
- YAML cannot express conditional branching without reinventing a template language
- Python closures can't be pickled — checkpointing YAML+callbacks is impossible
- Python is the native language of AI developers
- FastAPI-style pattern: declarative API over imperative language

**ADR:** `docs/adr/001-python-dsl-not-yaml.md`

### 2. Runtime Interpretation (NOT Code Generation)

**Decision:** The engine interprets the loop definition at runtime. No intermediate code is generated.

**Rationale:**
- Generated code is a black box — debugging is miserable
- Hot-reloading of loop definitions
- Simpler architecture — no compiler, no code gen bugs
- Matches how Terraform, Ansible, GitHub Actions work (YAML/intent → runtime interprets)

**ADR:** `docs/adr/002-runtime-interpretation.md`

### 3. Data-Only Checkpoints (NOT Closure Serialization)

**Decision:** Checkpoints store ONLY data: loop name, user semver, computed source hash, executed step names, context dict, step results, recorded responses. The loop definition is NEVER checkpointed.

**Rationale:**
- Python closures close over modules, env, tool instances — not picklable
- cloudpickle serializes the entire module — version-sensitive, fragile
- On resume: load loop from source by name+version, verify source hash, re-execute body with skip-list
- Checkpoints are version-bound with integrity check (source hash)
- `executed_step_names` (not step_index) enables resume after dynamic control flow

**ADR:** `docs/adr/003-data-only-checkpoints.md`

### 4. Custom Concurrency Supervisor (NOT asyncio.TaskGroup)

**Decision:** Use a custom supervisor for parallel step execution, NOT `asyncio.TaskGroup`.

**Rationale:**
- TaskGroup cancels ALL tasks on any exception — incompatible with error recovery
- ErrorPolicy needs to retry/skip/fallback per-step — TaskGroup doesn't allow this
- Options: `aiotools.PersistentTaskGroup`, `asyncio.gather(return_exceptions=True)`, or custom
- Custom supervisor gives full control over failure semantics

**ADR:** `docs/adr/004-custom-supervisor.md`

### 5. Immutable Context Passing

**Decision:** Each step receives an immutable snapshot of the context and returns a diff that gets merged.

**Rationale:**
- Mutable context + parallel steps = race conditions
- Immutable snapshots enable safe parallel execution
- Deterministic checkpointing (no mid-mutation snapshots)
- Matches production patterns (LangGraph, OpenAI Agents SDK)

**ADR:** `docs/adr/005-immutable-context.md`

### 6. MCP as Transport, NOT Facade

**Decision:** MCP is a thin transport layer. The Loop Protocol is its own contract for lifecycle management.

**Rationale:**
- MCP is request-response; loops run minutes — need events
- Loop Protocol defines: discover, start, subscribe_events, pause, resume, cancel, get_status
- Events via OpenTelemetry spans + minimal LoopEvent schema
- OTel = infrastructure-compatible; LoopEvent = agent-compatible

**ADR:** `docs/adr/006-mcp-as-transport.md`

### 7. Cost Tracking as Middleware

**Decision:** Cost tracking is a pluggable middleware, not core engine logic.

**Rationale:**
- Separates cost tracking from loop runtime — testable independently
- Users can override pricing without modifying the library
- Model-specific pricing is complex (tier, context position, thinking tokens)
- Budget enforcement via ErrorPolicy on BudgetExceeded exception

**ADR:** `docs/adr/007-cost-as-middleware.md`

### 8. Agent Interaction via Adapter Pattern

**Decision:** AgentAdapter base class with concrete implementations per agent type.

**Rationale:**
- Safe config modification: snapshot → atomic write → verify → rollback on failure
- Prompt injection via HTML comment markers (never overwrites)
- Auto-discovery of installed agents
- Each agent has different config file locations and formats

**ADR:** `docs/adr/008-agent-adapter-pattern.md`

### 9. ErrorPolicy Objects

**Decision:** Explicit ErrorPolicy objects for error recovery, not ad-hoc try/except.

**Rationale:**
- Different errors need different recovery: tool failure → retry, hallucination → re-prompt, schema error → trim+retry
- ErrorPolicy is configurable per error type
- Default policies provided, users can override
- Makes error handling explicit, testable, and configurable

**ADR:** `docs/adr/009-error-policy.md`

### 10. Deterministic Replay

**Decision:** Record all LLM responses. Replay mode injects recorded responses instead of calling the model.

**Rationale:**
- Essential for debugging: step 47 produced bad output — replay from step 46 with recorded response
- Enables A/B testing of different callbacks without burning API credits
- Deterministic replay requires recording both LLM responses AND tool results

**ADR:** `docs/adr/010-deterministic-replay.md`
