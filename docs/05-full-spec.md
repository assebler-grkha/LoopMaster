# LoopMaster — Full Specification Summary

## Project Identity

- **Name:** LoopMaster
- **Type:** Python library + thin CLI for defining, validating, executing, and debugging AI agent loops
- **Repository:** git@github.com:assebler-grkha/LoopMaster.git
- **Stack:** Python 3.11+, pydantic v2, typer, structlog, asyncio, pytest
- **License:** MIT (proposed)

## What It Does

LoopMaster is a **loop engine** for AI agent systems. It lets developers:

1. Define loops in Python DSL (@Loop, Step, Parallel)
2. Validate loop topology before execution
3. Execute loops with built-in checkpointing, error recovery, cost tracking
4. Safely interact with agent applications (read/write config, inject prompts)
5. Measure loop efficiency via metrics
6. Resume after interruptions (context overflow, crashes, agent restarts)

## Key Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Source of truth | Python DSL (NOT YAML) | YAML can't express branching; closures can't be pickled |
| Execution | Runtime interpretation (NOT code gen) | Generated code is black box; debugging impossible |
| Runtime model | Lazy Step() execution | Step() runs immediately, stores result in context, tracks executed_step_names |
| Checkpoints | Data-only (no code serialization) | Closures not picklable; source hash for integrity |
| Concurrency | Custom supervisor (NOT TaskGroup) | TaskGroup kills all on error; incompatible with ErrorPolicy |
| Context | Immutable snapshots + diff merging | Safe parallel execution; deterministic checkpoints |
| MCP | Transport layer (NOT facade) | MCP is request-response; loops need events |
| Cost tracking | Middleware (NOT core) | Separable, testable, overridable |
| Agent interaction | Adapter pattern | Each agent has different config format |
| Error handling | ErrorPolicy objects | Explicit, configurable, testable |
| Replay | Record all LLM responses | Essential for debugging; enables A/B testing |

## Package Structure

```
loopmaster/
├── core/           # Engine, types, context, supervisor, exceptions
├── checkpoint/     # Checkpoint creation, loading, versioning
├── recovery/       # ErrorPolicy, interruption protection
├── cost/           # Cost tracking middleware, pricing tables
├── metrics/        # MetricsCollector, exporters
├── agents/         # AgentAdapter, ConfigManager, PromptManager
├── mcp/            # MCP transport, Loop Protocol
├── events/         # EventEmitter (OTel + LoopEvent)
├── cli/            # Typer CLI commands
├── templates/      # 7 loop templates
└── utils/          # LLM abstraction, serialization
```

## DSL Examples

### Basic Reflection Loop

```python
from loopmaster import Loop, Step

@Loop(name="reflection", budget=Budget(max_cost="$1.00"))
def reflection_loop(ctx):
    Step("generate", model="gpt-4", prompt="Write about: {topic}")
    Step("critique", model="gpt-4", prompt="Critique this: {generate}")
    Step("revise", model="gpt-4", prompt="Revise based on: {critique}")
    return Step("final", model="gpt-4", prompt="Final version: {revise}")
```

### Multi-Agent with Parallel

```python
from loopmaster import Loop, Step, Parallel

@Loop(name="research_team")
def research_loop(ctx):
    Parallel(
        Step("web_search", tool="web_search", input=ctx.query),
        Step("doc_search", tool="doc_search", input=ctx.query),
        Step("api_search", tool="api_search", input=ctx.query),
    )
    Step("synthesize", model="gpt-4",
         prompt="Combine findings: {web_search}, {doc_search}, {api_search}")
    return Step("report", model="gpt-4", prompt="Report: {synthesize}")
```

### With Agent Attachment

```python
@Loop(name="refactor", agent="opencode", budget="$2.00")
def refactor_loop(ctx):
    Step("analyze", tool="codebase_memory", prompt="Analyze module {module_name}")
    Step("execute", tool="opencode_subagent", prompt="Execute plan: {plan}")
    Step("verify", tool="aislop_scan", prompt="Verify changes")
```

## User Flow (10 Steps)

1. User/agent defines need for a loop
2. `loop-engine init` creates project structure
3. User writes loop in Python DSL (@Loop, Step, Parallel)
4. Adds conditions with native Python if/else
5. `loop-engine validate` checks topology, variables, budget, models
6. `loop-engine run --dry-run` shows path + estimated cost without API calls
7. `loop-engine run` executes with real LLM calls, shows per-step metrics
8. Auto-checkpointing on failure, `--resume` to continue from checkpoint
9. YAML export for linear loops (warning for complex control flow)
10. Parallel execution via Parallel() with custom supervisor

## CLI Commands

```bash
loop-engine init [--agent opencode|claude_code|cursor]   # Create project
loop-engine validate [loop_file.py]                       # Validate DSL
loop-engine run [loop_file.py] [--dry-run] [--resume]    # Execute loop
loop-engine templates [list|show|create]                  # Manage templates
loop-engine docs [open|serve]                             # Documentation
```

## Templates (7)

1. **Reflection** — generate → critique → revise → final
2. **Tool Use** — call tools → process results → respond
3. **Planning** — plan → execute steps → verify → adapt
4. **Multi-Agent** — parallel agents → synthesize → resolve conflicts
5. **Critique** — generate → adversarial critique → revise → defend
6. **Escalation** — try simple → escalate to complex on failure
7. **Hybrid** — combination of above patterns

## Metrics Summary

**Add:** guardrail violation rate, escalation rate, idempotency violation rate, agent intervention count, context drift score, P50/P95/P99, variance across models, cost-quality Pareto frontier

**Remove:** cache hit rate, concurrency utilization, checkpoint frequency

**Replace:** token efficiency → output/input ratio

**Rename:** quality score → "quality signal (unreliable)"

**Storage:** in-memory (real-time) → SQLite (post-run) → external (Prometheus/PostHog) via collector pattern

## Interruption Protection

**Detection:** heartbeat, step acknowledgment, session state file
**State saving:** pre-step, post-step, emergency checkpoints (stores executed_step_names, not step_index)
**Recovery:** agent returns manually, master restarted, context overflow (compress and resume)

## Agent Interaction

**Adapter pattern:** AgentAdapter base → OpenCodeAdapter, ClaudeCodeAdapter, CursorAdapter
**Safe modification:** snapshot → atomic write → verify → rollback
**Prompt injection:** HTML comment markers (`<!-- LOOP_ENGINEER:start -->`)
**Key principle:** NEVER overwrites agent files; creates temporary section, reverts on completion

## MCP Integration

**MCP:** thin transport layer (discovery, trigger)
**Loop Protocol:** own contract (subscribe_events, pause, resume, cancel, get_status)
**Events:** OpenTelemetry spans + minimal LoopEvent schema

## Testing Strategy

- Unit tests for each module (core, checkpoint, recovery, cost, metrics, agents)
- Integration tests for full loop execution
- Mock LLM responses for deterministic testing
- Deterministic replay for regression tests
- CLI end-to-end tests

## Implementation Order

1. Core engine + types + context (foundation)
2. Checkpoint manager (data-only)
3. ErrorPolicy + recovery
4. Cost tracking middleware
5. Metrics collector
6. Agent adapters + config/prompt managers
7. CLI wrapper
8. Templates
9. MCP transport + Loop Protocol
10. Interruption protection
11. Deterministic replay
12. Documentation + examples
