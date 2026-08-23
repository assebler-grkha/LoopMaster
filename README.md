# LoopMaster

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-374%20passing-brightgreen.svg)](#development)
[![aislop](https://img.shields.io/badge/aislop-100%2F100%20healthy-brightgreen.svg)](#project-quality)

**Production-grade runtime engine and orchestrator for AI agent loops.**

Define agent loops as clean, readable Python code. LoopMaster handles execution, conditional branching, deterministic tool execution, real-time SSE streaming, LLM client recovery, OpenTelemetry distributed tracing, persistent SQLite job stores, versioned checkpoint migrations, and budget enforcement.

```python
from loopmaster import Loop, Step, Conditional, LoopEngine, Budget
from loopmaster.executors import ShellExecutor

@Loop(name="smart-code-review", version="1.0.0")
def review_pipeline(ctx):
    # 1. Run local test suite using deterministic ShellExecutor
    Step(
        "run_tests",
        executor=ShellExecutor(command="pytest tests/unit", timeout=30.0),
    )

    # 2. Dynamic conditional branching based on test results
    Conditional(
        name="branch_on_tests",
        condition=lambda c: c.get("run_tests", {}).get("success", False),
        then_steps=[
            Step("summarize", model="gpt-4o-mini", prompt="Summarize passing tests: {run_tests.stdout}"),
            Step("tag_release", prompt="Tag candidate for staging"),
        ],
        else_steps=[
            Step("diagnose", model="claude-3-5-sonnet", prompt="Diagnose failure: {run_tests.stderr}"),
            Step("suggest_fix", model="claude-3-5-sonnet", prompt="Suggest patch for {diagnose}"),
        ],
    )

    # 3. Final notification
    Step("notify", prompt="Send summary notification")
    return ctx

engine = LoopEngine(budget=Budget(max_cost=2.00, max_tokens=50000))
result = engine.run(review_pipeline, {"target_branch": "main"})
```

---

## Table of Contents

- [What is LoopMaster?](#what-is-loopmaster)
- [Key Features](#key-features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [DSL Reference](#dsl-reference)
  - [Steps](#steps)
  - [Parallel Steps](#parallel-steps)
  - [Conditional Branching](#conditional-branching)
  - [Context Passing & Dot-Notation](#context-passing--dot-notation)
  - [Tool Execution Bridge](#tool-execution-bridge)
  - [Error Recovery & Policies](#error-recovery--policies)
  - [Budget Enforcement](#budget-enforcement)
  - [Interruption Protection & Heartbeats](#interruption-protection--heartbeats)
  - [Checkpoints & SemVer Migration](#checkpoints--semver-migration)
  - [Deterministic Replay](#deterministic-replay)
  - [YAML Export](#yaml-export)
- [Observability & Streaming](#observability--streaming)
  - [OpenTelemetry & OTLP Tracing](#opentelemetry--otlp-tracing)
  - [Real-Time SSE Streaming](#real-time-sse-streaming)
  - [Metrics & Cost Tracking (SQLite)](#metrics--cost-tracking-sqlite)
- [Model Context Protocol (MCP) Server](#model-context-protocol-mcp-server)
- [Agent Adapters](#agent-adapters)
- [Built-in Templates](#built-in-templates)
- [CLI Reference](#cli-reference)
- [Supported Providers](#supported-providers)
- [Architecture](#architecture)
- [Development & Quality](#development--quality)
- [License](#license)

---

## What is LoopMaster?

LoopMaster is a **runtime engine** for agent loops.

### What it is NOT:
- **Not an all-in-one agent framework** — it doesn't force a proprietary agent abstraction or rigid prompt template language.
- **Not a heavy workflow engine** — no complex DAG configs, no UI editors, no mandatory heavy database dependencies.
- **Not just a wrapper** — it provides enterprise runtime guarantees: cost control, persistent resume, tracing, and deterministic replays.

### What it IS:
- A **Python DSL** for defining loops with `@Loop`, `Step()`, `Parallel()`, and `Conditional()`.
- A **Runtime Engine** with automatic retry, model fallbacks, budget limits, and checkpoint/resume.
- A **Tool Execution Bridge** with zero-dependency `ShellExecutor`, `HTTPExecutor`, and `MCPToolExecutor`.
- A **Streaming & Observability System** with W3C SSE, OpenTelemetry (OTLP Proto3 JSON), and SQLite metrics export.
- An **Adapter System** for OpenCode, Claude Code, Cursor, and custom agent environments.
- A **FastMCP Server** with SQLite WAL persistent job queues.

---

## Key Features

- 🐍 **Declarative Python DSL**: Define loops cleanly with `@Loop`, `Step()`, `Parallel()`, and `Conditional()`.
- 🔀 **Conditional Branching**: Dynamic branching with safe AST whitelist evaluation (zero `eval()` security risks) and branch stickiness on resume.
- 🛠️ **Tool Execution Bridge**: Deterministic stdlib execution for Shell commands, REST/HTTP webhooks, and external MCP tools.
- 🌊 **Real-Time SSE Streaming**: Native Server-Sent Events with streaming token chunks for OpenAI, Anthropic, and Google Gemini.
- 📡 **OpenTelemetry & OTLP Tracing**: Pure stdlib distributed tracing (`/v1/traces`, `/v1/metrics`), W3C `traceparent` propagation, and GenAI semantic conventions.
- 💾 **Persistent SQLite JobStore**: Thread-safe WAL SQLite database for MCP jobs with background resume and recovery across restarts.
- 🔄 **Loop Versioning & Checkpoint Migration**: SemVer-based compatibility policies and declarative BFS migration chains.
- 🛡️ **Budget & Interruption Protection**: Heartbeat monitors, max cost/token guards, pre/post step checkpoints, and fallback models.
- 🔌 **FastMCP Integration**: First-class Model Context Protocol server exposing `loop_run`, `loop_status`, `loop_result`, and `loop_events`.
- 🧪 **Deterministic Replay**: Record live LLM responses and replay them deterministically in test suites without network calls.

---

## Installation

```bash
pip install -e .

# With development tools (pytest, ruff, mypy)
pip install -e ".[dev]"
```

Requires **Python 3.11+**. Built with **zero heavy external runtime dependencies**.

---

## Quick Start

### 1. Create a loop from a template

```bash
loop-engine init my-loop --template reflection
```

This generates a runnable Python file. Available templates:

| Template | Description |
|---|---|
| `reflection` | Execute → evaluate → revise → repeat until quality threshold |
| `tool_use` | Agent uses tools in a loop, checking results each iteration |
| `planning` | Plan → execute → verify → replan cycle |
| `multi_agent` | Multiple agents collaborate in sequence |
| `critique` | Generate → critique → revise cycle |
| `escalation` | Try simple → complex → expert approaches |
| `hybrid` | Combines reflection + tool use + planning |

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

A `Step` is a single unit of work (an LLM invocation, deterministic tool call, or callback):

```python
Step("generate", model="gpt-4o", prompt="Write a summary about {topic}")
Step("search", tool="web_search", input={"query": "{topic}"})
```

Steps are **lazy** — they don't execute until `engine.run()` processes them.

### Parallel Steps

Execute multiple steps concurrently:

```python
from loopmaster import Parallel, Step

Parallel(
    Step("fetch_docs", prompt="Fetch documentation for {query}"),
    Step("fetch_issues", prompt="Fetch open issues for {query}"),
)
```

### Conditional Branching

The `Conditional` construct allows dynamic branch selection based on execution context:

```python
from loopmaster import Conditional, Step

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

### Context Passing & Dot-Notation

The `Context` object passes data safely between steps:

```python
@Loop(name="pipeline")
def pipeline(ctx):
    Step("step1", prompt="Process: {input}")
    # step1's output is merged and available as {step1} or {step1.field} in downstream steps
    Step("step2", prompt="Refine: {step1}")
    return ctx
```

- **Dot-Notation Resolution**: Access nested fields in templates e.g. `{run_tests.stdout}`, `{http_step.body.user.id}`, `{mcp_step.text}`.
- **Snapshot Pattern**: Context is immutable per-step — changes from one step don't affect others until explicitly merged.

### Tool Execution Bridge

Deterministic executors built with the Python standard library (zero external dependencies):

#### 1. ShellExecutor
```python
from loopmaster.executors import ShellExecutor

Step(
    "compile",
    executor=ShellExecutor(
        command="cargo build --release",
        timeout=60.0,
        cwd="./src",
    ),
)
```
- Safe cross-platform command splitting (`posix=sys.platform != "win32"` to prevent Windows path mangling).
- Session isolation (`os.setsid` on POSIX) and process tree termination (`taskkill /F /T` on Windows, process group kill on POSIX).
- Emits `tool.shell` OpenTelemetry client spans.

#### 2. HTTPExecutor
```python
from loopmaster.executors import HTTPExecutor

Step(
    "post_webhook",
    executor=HTTPExecutor(
        url="https://api.github.com/repos/{owner}/{repo}/issues",
        method="POST",
        headers={"Authorization": "Bearer {env.GITHUB_TOKEN}"},
        json_data={"title": "Bug Report", "body": "{diagnose.output}"},
        allowed_status=[200, 201],
    ),
)
```
- Comprehensive `HTTPError` body capture (never drops API error responses).
- Automatic handling of 204 No Content / empty bodies.
- Emits `tool.http` OpenTelemetry client spans.

#### 3. MCPToolExecutor
```python
from loopmaster.executors import MCPToolExecutor

Step(
    "query_db",
    executor=MCPToolExecutor(
        server_command=["npx", "-y", "@modelcontextprotocol/server-postgres"],
        tool_name="query",
        arguments={"sql": "SELECT * FROM users WHERE active = true"},
    ),
)
```
- Strict Newline-Delimited JSON (NDJSON) framing with full MCP handshake.
- Timeout protection using `ThreadPoolExecutor` and stderr deadlock prevention.
- Emits `tool.mcp` OpenTelemetry client spans.

### Model Registry & Semantic Aliases

Decouple loop steps from hardcoded providers using semantic aliases:

```python
from loopmaster import Loop, Step, LoopEngine, ModelRegistry, ModelPolicy, ModelPolicyMode

@Loop(name="portable-loop")
def portable_loop(ctx):
    Step("classify", model="@fast", prompt="Classify: {input}")
    Step("code_gen", model="@coding", prompt="Write solution for: {classify}")
    Step("deep_audit", model="@smart", prompt="Audit code: {code_gen}")
    return ctx

# Configure registry & policies
registry = ModelRegistry()
engine = LoopEngine(
    model_registry=registry,
    model_policy=ModelPolicy(
        mode=ModelPolicyMode.STRICT,      # Enforce approved models only
        max_cost_per_step=0.25,           # Prevent accidental massive prompt spend
        default_alias="@fast",
    ),
)
```

| Semantic Alias | Recommended For | Default Resolved Model |
|---|---|---|
| `@fast` | Classification, extraction, routing, simple JSON | `gpt-4o-mini` |
| `@smart` | Complex reasoning, architecture, deep audits | `gpt-4o` |
| `@coding` | Code generation, refactoring, test writing | `claude-3-5-sonnet` |
| `@cheap` | Background batch jobs, log parsing | `gemini-1.5-flash` |
| `@reasoning` | STEM / algorithmic problem solving | `o1-mini` |
| `@fallback` | Resilient recovery model for error policies | `gpt-4o-mini` |
| `@auto` | Dynamically auto-routed based on prompt size & budget | Adaptive |

See [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md) for full AI agent guidelines.

### Error Recovery & Policies

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

- **RETRY** — re-run the step (up to `retry` times with exponential backoff)
- **SKIP** — mark step as failed, continue with next
- **ABORT** — stop the loop immediately
- **FALLBACK** — switch to `fallback_model` and retry once

### Budget Enforcement

```python
from loopmaster import Budget

engine = LoopEngine(
    budget=Budget(
        max_cost=5.00,       # USD limit
        max_tokens=100000,   # total token limit
        max_steps=20,        # max step executions
    )
)
```

Budget is checked before each step. Raises `BudgetExceededError` when exceeded.

### Interruption Protection & Heartbeats

```python
from loopmaster import InterruptionProtection

engine = LoopEngine(
    interruption_protection=InterruptionProtection(
        enabled=True,
        heartbeat_interval=15.0,
        heartbeat_timeout=30.0,
        pre_step_checkpoint=True,
        post_step_checkpoint=True,
    )
)
```

### Checkpoints & SemVer Migration

#### Checkpoint Persistence
```python
from loopmaster.checkpoint import CheckpointManager

mgr = CheckpointManager("./checkpoints")
checkpoint = mgr.load_latest("smart-code-review")
result = engine.run(review_pipeline, {}, resume_checkpoint=checkpoint)
```

#### SemVer Migration Chains
When loop definitions evolve across versions, define declarative migration paths:

```python
from loopmaster.checkpoint.migration import MigrationRegistry, rename_checkpoint_step

registry = MigrationRegistry()

@registry.register_migration("smart-code-review", "1.0.0", "1.1.0")
def migrate_v1_to_v1_1(cp):
    # Rename legacy step in state, context, and completed results
    rename_checkpoint_step(cp, "run_tests", "execute_unit_tests")
    cp.context_data["migrated"] = True
    return cp
```

### Deterministic Replay

Record and replay loop execution for deterministic unit tests:

```python
from loopmaster.core.replay import ResponseRecorder, ReplayRunner

# Record responses during live run
recorder = ResponseRecorder()
# ... run loop with recorder active ...

# Replay deterministically in tests without API calls
runner = ReplayRunner(recorder.session)
result = runner.run(loop_def, {})
```

### YAML Export

Export your loop definition to YAML for documentation or visualization:

```bash
loop-engine export my_loop.py
```

Or programmatically:

```python
from loopmaster.core.yaml_export import export_loop
yaml_str = export_loop(loop_def)
```

---

## Observability & Streaming

### OpenTelemetry & OTLP Tracing

LoopMaster includes a native, lightweight OpenTelemetry subsystem without mandatory SDK dependencies:

```python
from loopmaster.telemetry import configure_telemetry

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
  - `tool.<name>` (CLIENT): tool execution (`tool.shell`, `tool.http`, `tool.mcp`).
  - `llm.<model>` (CLIENT): LLM client call with GenAI semantic conventions (`gen_ai.request.model`, `gen_ai.usage.*`).
- **Resilient Background Exporting**: Non-blocking background worker daemon that buffers spans and never slows down execution.

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

### Metrics & Cost Tracking (SQLite)

```python
from loopmaster.metrics import MetricsCollector
from loopmaster.cost import CostTracker
from loopmaster.metrics.sqlite_exporter import SQLiteExporter

collector = MetricsCollector("./metrics")
tracker = CostTracker()

engine = LoopEngine(
    metrics_collector=collector,
    cost_tracker=tracker,
    checkpoint_dir="./checkpoints",
)
result = engine.run(loop_def, {})

# Export to SQLite database
with SQLiteExporter("./metrics.db") as exp:
    exp.export_collector(collector)
```

---

## Model Context Protocol (MCP) Server

Run LoopMaster as a Model Context Protocol (MCP) server:

```bash
python scripts/loopmaster_mcp.py
```

### Persistent SQLite JobStore
MCP jobs are backed by a thread-safe WAL SQLite database (`.loopmaster/jobs.db`), ensuring job state and results survive server restarts.

### Supported MCP Tools

| Tool | Description |
|---|---|
| `loop_run` | Execute or start an asynchronous persistent loop job |
| `loop_status` | Query execution status, current step, and progress (0.0–1.0) |
| `loop_result` | Retrieve full outputs, token counts, and cost breakdowns |
| `loop_cancel` | Cancel an active running job |
| `loop_list` | List available loops discovered across the workspace |
| `loop_get` | Get structural details, step definitions, and budget limits |

---

## Agent Adapters

LoopMaster integrates with agent applications via adapters:

```python
from loopmaster.agents import AgentRegistry

registry = AgentRegistry()

# Auto-discover installed agents
agents = registry.discover_all()

# Get a specific adapter
adapter = registry.get_adapter("opencode")
adapter.inject_loop_context("Run this loop: ...")
```

### Supported Agents

| Agent | Config Location | System Prompt |
|---|---|---|
| **OpenCode** | `~/.opencode/config.json` | `~/.opencode/prompt.md` |
| **Claude Code** | `~/.claude/settings.json` | `~/.claude/CLAUDE.md` |
| **Cursor** | `~/.cursor/settings.json` | `~/.cursor/rules` |
| **Custom** | User-specified paths | User-specified paths |

### Custom Adapter
```python
from loopmaster.agents import CustomAdapter

adapter = CustomAdapter(
    config_path="/path/to/config.json",
    prompt_path="/path/to/prompt.md",
)
```

---

## Built-in Templates

Generate loop code from built-in patterns:

```python
from loopmaster.templates import generate_code, list_templates

# List available templates
templates = list_templates()

# Generate runnable Python source code
code = generate_code(
    "reflection",
    name_var="my_loop",
    task="analyze code quality",
    tool="static_analyzer",
)
```

---

## CLI Reference

| Command | Description |
|---|---|
| `loop-engine init <name>` | Create a new loop file from default template |
| `loop-engine init <name> --template reflection` | Use a specific template |
| `loop-engine init <name> --task "analyze code"` | Custom task description |
| `loop-engine validate <file>` | Validate a loop file (no execution) |
| `loop-engine run <file>` | Execute a loop |
| `loop-engine run <file> --resume` | Resume from last checkpoint |
| `loop-engine run <file> --dry-run` | Validate only, no execution |
| `loop-engine checkpoints <name>` | List checkpoints for a loop |
| `loop-engine templates` | List available templates |
| `loop-engine export <file>` | Export loop to YAML |
| `loop-engine docs` | Open documentation directory |

---

## Supported Providers

| Провайдер | API Key Env | Base URL | Модели |
|---|---|---|---|
| **OpenAI** | `OPENAI_API_KEY` | `https://api.openai.com/v1` | gpt-4o, gpt-4o-mini, gpt-4-turbo |
| **Anthropic** | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | claude-3-5-sonnet, claude-3-opus |
| **Google** | `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com` | gemini-1.5-pro, gemini-1.5-flash |
| **OpenRouter** | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | Любые модели |
| **Custom** | `CUSTOM_API_KEY` | Произвольный URL | Любые OpenAI-совместимые |

---

## Architecture

```
loopmaster/
├── core/                  # Runtime engine & coordinator
│   ├── engine.py          # LoopEngine — execution loop & lifecycle
│   ├── runner.py          # BlockExecContext & block runner
│   ├── condition.py       # Safe AST condition parser (zero eval)
│   ├── policies.py        # ErrorPolicy, Budget, InterruptionProtection
│   ├── types.py           # DSL types: Step, Parallel, Conditional, Loop
│   ├── context.py         # Context state management & snapshots
│   ├── state.py           # Run state initialization & checkpoint helpers
│   ├── step_executor.py   # Step execution, retries, and GenAI spans
│   ├── heartbeat.py       # Interruption protection & background monitor
│   ├── replay.py          # Deterministic replay for testing
│   ├── exceptions.py      # Error hierarchy
│   ├── supervisor.py      # Async concurrency supervisor
│   └── yaml_export.py     # YAML serialization
├── executors/             # Tool Execution Bridge
│   ├── base.py            # BaseExecutor ABC & dot-path resolver
│   ├── shell.py           # ShellExecutor (process tree kill, os.setsid)
│   ├── http.py            # HTTPExecutor (urllib, HTTPError capture)
│   └── mcp.py             # MCPToolExecutor (stdio NDJSON client)
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
├── checkpoint/            # Checkpoint persistence & migration
│   ├── __init__.py        # CheckpointManager (JSON persistence)
│   └── migration.py       # SemVer parser, BFS migration registry
├── metrics/               # Observability & cost metrics
│   ├── collector.py       # MetricsCollector & OTLP metric payload
│   └── sqlite_exporter.py # SQLite export & query utilities
├── cost/                  # Cost tracking
│   └── tracker.py         # Token & dollar tracking per model
├── agents/                # Agent adapters (OpenCode, Claude Code, Cursor)
│   ├── base.py            # AgentAdapter ABC + AgentInfo
│   ├── adapter.py         # Concrete adapters
│   ├── registry.py        # Auto-discovery and lookup
│   ├── config_manager.py  # Safe config modification (snapshot/rollback)
│   └── prompt_manager.py  # Marker-based prompt injection
├── templates/             # Loop templates
│   └── __init__.py        # 7 templates + code generation
├── mcp/                   # Persistent JobStore & Discovery
│   ├── job_store.py       # WAL SQLite persistent job storage
│   └── discovery.py       # Loop discovery and recursive serialization
└── cli/                   # Command-line interface
    └── app.py             # Typer CLI commands
```

---

## Development & Quality

```bash
# Install with dev tools
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=loopmaster

# Lint & Format
ruff check src/ scripts/ tests/
ruff format src/ scripts/ tests/

# Type check
mypy src/

# Run code quality audit
npx aislop scan
```

### Project Quality

- **362 tests** passing across 29 test suites (100% pass rate)
- **aislop score: 100/100 Healthy**
- Modular architecture (all files under 370 lines, all functions under 70 lines, parameter counts <= 6)
- Pure Python standard library foundation with zero external runtime dependencies

---

## License

MIT
