# LoopMaster

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-349%20passing-brightgreen.svg)](#development)
[![aislop](https://img.shields.io/badge/aislop-100%2F100%20healthy-brightgreen.svg)](#project-quality)

**Production-ready runtime engine and orchestrator for AI agent loops.**

Define agent loops as clean Python code. LoopMaster handles execution, conditional branching, real-time SSE streaming, LLM client recovery, OpenTelemetry distributed tracing, persistent SQLite job stores, versioned checkpoint migrations, and budget enforcement.

```python
from loopmaster import Loop, Step, Conditional, LoopEngine, Budget

@Loop(name="smart-triage", version="1.0.0")
def triage_loop(ctx):
    # 1. Analyze input with LLM
    Step("classify", model="gpt-4o-mini", prompt="Classify issue: {issue_text}")

    # 2. Dynamic conditional branching
    Conditional(
        condition=lambda c: c.get("classify", {}).get("is_bug", False),
        then_steps=[
            Step("reproduce", model="claude-3-5-sonnet", prompt="Write repro test for {issue_text}"),
            Step("generate_patch", model="claude-3-5-sonnet", prompt="Generate fix patch"),
        ],
        else_steps=[
            Step("answer_faq", model="gpt-4o-mini", prompt="Answer as general question"),
        ],
    )

    # 3. Final step
    Step("notify", prompt="Format final report")
    return ctx

engine = LoopEngine(budget=Budget(max_cost=2.00, max_tokens=50000))
result = engine.run(triage_loop, {"issue_text": "Null pointer in auth handler"})
```

---

## Key Features

- 🐍 **Declarative Python DSL**: Define loops with `@Loop`, `Step()`, and `Conditional()` branching.
- 🔀 **Conditional Branching**: Dynamic control flow with safe AST whitelist evaluation and branch stickiness on resume.
- 🌊 **Real-Time SSE Streaming**: Native Server-Sent Events with streaming token chunks for OpenAI, Anthropic, and Google Gemini.
- 📡 **OpenTelemetry & OTLP Tracing**: Pure stdlib distributed tracing (`/v1/traces`, `/v1/metrics`), W3C `traceparent` context propagation, and GenAI semantic conventions.
- 💾 **Persistent SQLite JobStore**: Thread-safe WAL SQLite database for MCP jobs with background resume and recovery across restarts.
- 🔄 **Loop Versioning & Checkpoint Migration**: SemVer-based compatibility policies and declarative BFS migration chains.
- 🛡️ **Budget & Interruption Protection**: Heartbeat monitors, max cost/token guards, pre/post step checkpoints, and fallback models.
- 🔌 **FastMCP Integration**: First-class Model Context Protocol server exposing `loop_run`, `loop_status`, `loop_result`, and `loop_events`.

---

## Installation

```bash
pip install -e .

# With development tools (pytest, ruff, mypy)
pip install -e ".[dev]"
```

Requires **Python 3.11+**. Zero heavy external runtime dependencies.

---

## Quick Start

### 1. Create a loop from a template

```bash
loop-engine init my-loop --template reflection
```

Available templates: `reflection`, `tool_use`, `planning`, `multi_agent`, `critique`, `escalation`, `hybrid`.

### 2. Validate (no LLM calls)

```bash
loop-engine validate my_loop.py
```

### 3. Execute

```bash
loop-engine run my_loop.py
loop-engine run my_loop.py --resume    # resume from last checkpoint
loop-engine run my_loop.py --dry-run   # validate only, no execution
```

---

## DSL Reference

### Steps

A `Step` is a single unit of work (an LLM invocation, tool execution, or callback):

```python
Step("generate", model="gpt-4o", prompt="Write a story about {topic}")
Step("search", tool="web_search", input={"query": "{prompt}"})
```

### Conditional Branching

The `Conditional` construct allows dynamic branch selection based on execution context:

```python
from loopmaster import Conditional

Conditional(
    name="check_status",
    condition="status == 'ready' and retry_count < 3",  # Safe AST evaluation
    then_steps=[
        Step("deploy", prompt="Deploy to production"),
    ],
    else_steps=[
        Step("escalate", prompt="Alert on-call engineer"),
    ],
)
```

- **Safe Evaluation**: Pure AST parsing (`ast.parse(mode='eval')`) with strict operator whitelisting (zero `eval()` security risks).
- **Branch Stickiness**: If resuming from a checkpoint where branch steps already executed, the engine remains locked to that branch regardless of subsequent context changes.

### Context

The `Context` object passes data safely between steps:

```python
@Loop(name="pipeline")
def pipeline(ctx):
    Step("step1", prompt="Process: {input}")
    # step1's output is merged and available as {step1} in downstream steps
    Step("step2", prompt="Refine: {step1}")
    return ctx
```

### Error Recovery & Fallbacks

```python
from loopmaster import LoopEngine, ErrorPolicy, RecoveryAction

engine = LoopEngine(
    error_policy=ErrorPolicy(
        retry=3,                          # max retries per step
        backoff=1.0,                      # seconds between retries
        on_failure=RecoveryAction.FALLBACK,
        fallback_model="gpt-4o-mini",     # fallback on primary failure
    )
)
```

---

## Observability & Telemetry

### OpenTelemetry (OTel / OTLP) Tracing

LoopMaster includes a native, lightweight OpenTelemetry subsystem without mandatory SDK dependencies:

```python
from loopmaster.telemetry import configure_telemetry

# Configure OTLP HTTP exporter (Jaeger, Tempo, Datadog)
configure_telemetry(
    exporter_type="otlp_http",
    service_name="agent-pipeline",
    otlp_endpoint="http://localhost:4318/v1/traces",
)
```

- **W3C TraceContext**: Propagates `traceparent` headers (`00-{trace_id}-{span_id}-{flags}`) across processes.
- **Trace Hierarchy**:
  - `loop.<name>` (INTERNAL): root loop span with total cost, tokens, and step count.
  - `conditional.<name>` (INTERNAL): conditional evaluation and branch selection.
  - `step.<name>` (INTERNAL): individual step execution duration and status.
  - `llm.<model>` (CLIENT): LLM client call with GenAI semantic conventions (`gen_ai.request.model`, `gen_ai.usage.*`).
- **Resilient Exporting**: Non-blocking background daemon worker with queue buffering that never fails or slows down user loops.

### Real-Time SSE Streaming

Subscribe to real-time events over Server-Sent Events (SSE):

```python
from loopmaster.events import EventEmitter

emitter = EventEmitter()
engine = LoopEngine(event_emitter=emitter)

# Subscribe to streaming chunks and lifecycle events
emitter.on("step_chunk", lambda ev: print(ev.payload["text"], end="", flush=True))
emitter.on("branch_selected", lambda ev: print(f"Branched to: {ev.payload['branch']}"))
```

---

## Checkpoints & Migrations

### Resuming Checkpoints

```python
from loopmaster.checkpoint import CheckpointManager

mgr = CheckpointManager("./checkpoints")
checkpoint = mgr.load_latest("smart-triage")
result = engine.run(triage_loop, {}, resume_checkpoint=checkpoint)
```

### SemVer Migration Chains

When loop definitions evolve across versions, define declarative migration paths:

```python
from loopmaster.checkpoint.migration import MigrationRegistry, rename_checkpoint_step

registry = MigrationRegistry()

@registry.register_migration("smart-triage", "1.0.0", "1.1.0")
def migrate_v1_to_v1_1(cp):
    # Rename legacy step in state, context, and completed results
    rename_checkpoint_step(cp, "classify", "analyze_issue")
    cp.context_data["migrated"] = True
    return cp
```

---

## MCP Server Integration

Run LoopMaster as a Model Context Protocol (MCP) server:

```bash
python scripts/loopmaster_mcp.py
```

### Supported MCP Tools

| Tool | Description |
|---|---|
| `loop_run` | Execute or start an asynchronous persistent loop job |
| `loop_status` | Query execution status, current step, and progress |
| `loop_result` | Retrieve full outputs, token counts, and cost breakdowns |
| `loop_cancel` | Cancel an active running job |
| `loop_list` | List available loops discovered across the workspace |
| `loop_get` | Get structural details, step definitions, and budget limits |

---

## Architecture

```
loopmaster/
├── core/                  # Core runtime engine
│   ├── engine.py          # LoopEngine coordinator
│   ├── runner.py          # BlockExecContext & step/conditional runner
│   ├── condition.py       # Safe AST condition parser
│   ├── types.py           # DSL types: Step, Conditional, Loop, Budget
│   ├── context.py         # Context state management & snapshots
│   ├── state.py           # Run state initialization & checkpoint helpers
│   ├── step_executor.py   # Step execution, retries, and GenAI spans
│   ├── heartbeat.py       # Interruption protection & background monitor
│   └── exceptions.py      # Error hierarchy
├── telemetry/             # OpenTelemetry & OTLP subsystem
│   ├── types.py           # Span, SpanContext, SpanKind, SpanStatus
│   ├── context.py         # ContextVar isolation & W3C traceparent
│   ├── tracer.py          # Tracer & NoOpTracer implementations
│   ├── exporter.py        # OTLPHttpSpanExporter, JsonFile, InMemory
│   └── provider.py        # Global telemetry provider & configuration
├── llm/                   # Multi-provider LLM client
│   ├── client.py          # Unified OpenAI, Anthropic, Gemini client
│   ├── streaming.py       # Streaming SSE response parsers
│   └── types.py           # LLMConfig, StreamChunk, exceptions
├── events/                # Event system & SSE formatters
│   ├── __init__.py        # EventEmitter & event history buffer
│   └── sse.py             # W3C SSE formatters & SSEStream
├── checkpoint/            # Checkpoint storage & migrations
│   ├── __init__.py        # CheckpointManager (JSON persistence)
│   └── migration.py       # SemVer parser, BFS migration registry
├── metrics/               # Observability & cost metrics
│   ├── collector.py       # MetricsCollector & OTLP metric payload
│   └── sqlite_exporter.py # SQLite export & query utilities
├── cost/                  # Cost tracking
│   └── tracker.py         # Token & dollar tracking per model
├── agents/                # Agent adapters (OpenCode, Claude Code, Cursor)
├── mcp/                   # Persistent JobStore & Discovery
│   ├── job_store.py       # WAL SQLite persistent job storage
│   └── discovery.py       # Loop discovery and recursive serialization
└── cli/                   # Command-line interface
    └── app.py             # Typer CLI commands
```

---

## Development

```bash
# Install with dev tools
pip install -e ".[dev]"

# Run tests
pytest

# Lint & Format
ruff check src/ scripts/ tests/
ruff format src/ scripts/ tests/

# Type check
mypy src/

# Run code quality audit
npx aislop scan
```

### Project Quality

- **349 tests** passing across 28 test suites
- **aislop score: 100/100 Healthy**
- Modular architecture (< 400 lines per file, < 80 lines per function, <= 6 parameters)
- Zero external runtime dependencies

---

## License

MIT
