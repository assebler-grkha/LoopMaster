# LoopMaster

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-261%20passing-brightgreen.svg)](#development)

**Loop engine for AI agent systems.**

Define agent loops as Python code. LoopMaster handles execution, error recovery, cost tracking, checkpoints, and interruption protection — so you can focus on what your agents do, not how they run.

```python
from loopmaster import Loop, Step, LoopEngine, Budget

@Loop(name="research-loop", version="0.1.0")
def research(ctx):
    Step("search", model="gpt-4", prompt="Find info about {topic}")
    Step("summarize", model="gpt-4", prompt="Summarize: {search}")
    Step("critique", model="gpt-4", prompt="Critique this summary: {summarize}")
    return ctx

engine = LoopEngine(budget=Budget(max_cost=2.00))
result = engine.run(research, {"topic": "quantum computing"})
```

---

## What is LoopMaster?

LoopMaster is a **runtime engine** for agent loops. It is **not**:

- An **agent framework** — it doesn't provide LLM clients, tools, or prompts
- A **workflow engine** — no DAGs, no visual editors, no YAML configs
- A **monitoring system** — it tracks costs and metrics, but doesn't build dashboards

It **is**:

- A **Python DSL** for defining loops with `@Loop` and `Step()`
- A **CLI** for running, validating, exporting, and debugging loops
- A **runtime** with error recovery, budget enforcement, and checkpoint/resume
- An **adapter system** for OpenCode, Claude Code, Cursor, and custom agents
- A **metrics + cost tracking** system with SQLite export

---

## Installation

```bash
pip install -e .

# With development tools (pytest, ruff, mypy)
pip install -e ".[dev]"
```

Requires **Python 3.11+**.

---

## Quick Start

### 1. Create a loop from a template

```bash
loop-engine init my-loop --template reflection
```

This generates a runnable Python file. Available templates:

| Template | Description |
|----------|-------------|
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

A `Step` is a single unit of work — an LLM call or tool invocation:

```python
Step("generate", model="gpt-4", prompt="Write a story about {topic}")
Step("search", tool="web_search", input={"query": "{prompt}"})
```

Steps are **lazy** — they don't execute until `engine.run()` processes them.

### Context

The `Context` object passes data between steps:

```python
@Loop(name="pipeline")
def pipeline(ctx):
    Step("step1", prompt="Process: {input}")
    # step1's output is available as {step1} in subsequent prompts
    Step("step2", prompt="Refine: {step1}")
    return ctx
```

Context is immutable per-step (snapshot pattern) — changes from one step don't affect others until explicitly merged.

### Error Recovery

```python
from loopmaster import LoopEngine, ErrorPolicy, RecoveryAction

engine = LoopEngine(
    error_policy=ErrorPolicy(
        retry=3,              # max retries per step
        backoff=1.0,          # seconds between retries
        on_failure=RecoveryAction.RETRY,  # RETRY, SKIP, ABORT, FALLBACK
        fallback_model="gpt-4o-mini",     # used with FALLBACK
    )
)
```

- **RETRY** — re-run the step (up to `retry` times)
- **SKIP** — mark step as failed, continue with next
- **ABORT** — stop the loop immediately
- **FALLBACK** — switch to `fallback_model` and retry once

### Budget Enforcement

```python
from loopmaster import Budget

engine = LoopEngine(
    budget=Budget(
        max_cost=5.00,      # USD limit
        max_tokens=100000,   # token limit
        max_steps=20,        # step count limit
    )
)
```

Budget is checked before each step. Raises `BudgetExceededError` when exceeded.

### Checkpoints & Resume

Checkpoints are saved automatically after each step:

```python
engine = LoopEngine(checkpoint_dir="./checkpoints")
result = engine.run(loop_def, {"name": "world"})

# Resume from last checkpoint
from loopmaster.checkpoint import CheckpointManager
mgr = CheckpointManager("./checkpoints")
checkpoint = mgr.load_latest("my-loop")
result = engine.run(loop_def, {}, resume_checkpoint=checkpoint)
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

### Metrics & Cost Tracking

Metrics and costs are tracked automatically when you pass collectors to the engine:

```python
from loopmaster.metrics import MetricsCollector
from loopmaster.cost import CostTracker

collector = MetricsCollector("./metrics")
tracker = CostTracker()

engine = LoopEngine(
    metrics_collector=collector,
    cost_tracker=tracker,
    checkpoint_dir="./checkpoints",
)
result = engine.run(loop_def, {})

# Export to SQLite
from loopmaster.metrics.sqlite_exporter import SQLiteExporter
with SQLiteExporter("./metrics.db") as exp:
    exp.export_collector(collector)
```

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
|-------|----------------|---------------|
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

## CLI Commands

| Command | Description |
|---------|-------------|
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

## Architecture

```
loopmaster/
├── core/                  # Runtime engine
│   ├── engine.py          # LoopEngine — main execution loop
│   ├── types.py           # DSL types: Step, Loop, ErrorPolicy, Budget
│   ├── context.py         # Immutable context passing
│   ├── replay.py          # Deterministic replay for testing
│   ├── heartbeat.py       # Interruption protection
│   ├── step_executor.py   # Step execution with retry/backoff
│   ├── exceptions.py      # All exception types
│   ├── supervisor.py      # Async concurrency supervisor
│   └── yaml_export.py     # YAML serialization
├── agents/                # Agent adapters
│   ├── base.py            # AgentAdapter ABC + AgentInfo
│   ├── adapter.py         # OpenCode, Claude Code, Cursor adapters
│   ├── registry.py        # Auto-discovery and lookup
│   ├── config_manager.py  # Safe config modification (snapshot/rollback)
│   └── prompt_manager.py  # HTML marker-based prompt injection
├── metrics/               # Observability
│   ├── collector.py       # In-memory metrics collection
│   └── sqlite_exporter.py # SQLite export + query helpers
├── cost/                  # Cost tracking
│   └── tracker.py         # Per-model cost tracking with budgets
├── events/                # Event system
│   └── __init__.py        # LoopEvent + EventEmitter
├── utils/                 # Shared utilities
│   └── __init__.py        # LLMProvider ABC, serialization, hashing
├── templates/             # Loop templates
│   └── __init__.py        # 7 templates + code generation
├── checkpoint/            # Checkpoint persistence
│   └── __init__.py        # JSON-based checkpoint save/load
├── mcp/                   # MCP protocol
│   └── __init__.py        # LoopProtocol + MCPServer
└── cli/                   # Command-line interface
    ├── __init__.py
    └── app.py             # Typer CLI with 7 commands
```

---

## Development

```bash
# Install with dev tools
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=loopmaster

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy src/
```

### Project Quality

- **261 tests** passing across 15+ test files
- **aislop score: 100/100**
- All source files under 400 lines
- Zero dead dependencies
- Google-style docstrings on public APIs

---

## Deterministic Replay

Record and replay loop execution for testing:

```python
from loopmaster.core.replay import ResponseRecorder, ReplayRunner

# Record responses during live run
recorder = ResponseRecorder()
# ... run loop with recorder active ...

# Replay deterministically
runner = ReplayRunner(recorder.session)
result = runner.run(loop_def, {})
```

---

## Templates

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

## License

MIT
