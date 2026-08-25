# LoopMaster — Agent Instructions

> Exhaustive reference for AI agents interacting with LoopMaster.
> Covers: DSL reference, CLI usage, MCP tools, creating custom loops, error recovery, and all usage scenarios.

---

## Table of Contents

1. [What is LoopMaster](#what-is-loopmaster)
2. [Quick Start](#quick-start)
3. [DSL Reference](#dsl-reference)
4. [CLI Commands](#cli-commands)
5. [MCP Tools](#mcp-tools)
6. [Creating Custom Loops](#creating-custom-loops)
7. [Error Recovery](#error-recovery)
8. [Budget Control](#budget-control)
9. [Checkpoints & Resume](#checkpoints--resume)
10. [Context Passing](#context-passing)
11. [Templates](#templates)
12. [Usage Scenarios](#usage-scenarios)
13. [Agent Adapter Integration](#agent-adapter-integration)
14. [Metrics & Cost Tracking](#metrics--cost-tracking)
15. [Common Patterns](#common-patterns)
16. [Troubleshooting](#troubleshooting)

---

## What is LoopMaster

LoopMaster is a **Python runtime engine** for AI agent loops. It executes `@Loop` decorated functions containing `Step()` calls — each step represents an LLM call or tool invocation.

**It is NOT:**
- An agent framework (no LLM clients, tools, or prompts built-in)
- A workflow engine (no DAGs, no visual editors)
- A monitoring system (tracks costs/metrics but no dashboards)

**It IS:**
- A Python DSL: `@Loop` + `Step()` + `Parallel()` + `Conditional()`
- A JSON engine: LoopSpec v1 (`schemas/loopspec-v1.schema.json`, `spec/loader.py` + `compiler.py`)
- A CLI: `loop-engine` commands (incl. `export --format json`, `block add/get/list`)
- A runtime: error recovery, budget enforcement, checkpoint/resume, detached & agent execution modes
- An adapter system: OpenCode, Claude Code, Cursor integration
- A metrics system: cost tracking + SQLite export (WAL `JobStore` + notifications outbox)
- An MCP server: 17 tools (loops, blocks, HITL, inbox, models)

**Installation:**
```bash
pip install -e .
# CLI: loop-engine (globally available)
```

**Python import:**
```python
from loopmaster import Loop, Step, Parallel, Context, LoopEngine, Budget, ErrorPolicy, RecoveryAction
```

---

## Quick Start

### Minimal loop
```python
from loopmaster import Loop, Step

@Loop(name="hello", version="0.1.0")
def hello_loop(ctx):
    Step("greet", model="gpt-4", prompt="Say hello to {name}")
    return ctx
```

### Run from CLI
```bash
loop-engine run hello_loop.py
```

### Run from Python
```python
from loopmaster import LoopEngine

engine = LoopEngine()
# Loop is auto-discovered from the function's _loop_def attribute
engine.run(hello_loop._loop_def, {"name": "World"})
```

### Validate without execution
```bash
loop-engine validate hello_loop.py
loop-engine run hello_loop.py --dry-run
```

---

## DSL Reference

### @Loop decorator

```python
from loopmaster import Loop

@Loop(
    name="my-loop",           # Required: unique loop identifier
    version="1.0.0",          # Required: semantic version
    agent="opencode",         # Optional: target agent adapter
    budget="$5.00",           # Optional: cost/token/step budget
)
def my_loop(ctx):
    # Steps go here
    return ctx
```

The `@Loop` decorator:
- Creates a `LoopDef` dataclass attached as `my_loop._loop_def`
- Registers steps via thread-local collection during execution
- Supports string budget: `"$5.00"`, `"$10"`, `"100000"`, `"20 steps"`

### Step

```python
from loopmaster import Step

Step(
    name="step_name",         # Required: unique within the loop
    model="gpt-4",            # Optional: LLM model identifier
    tool="web_search",        # Optional: tool name (alternative to model)
    prompt="Do something with {variable}",  # Optional: template string
    input={"query": "{prev_step}"},         # Optional: tool input dict
    retry=3,                  # Optional: per-step retry count (None = use ErrorPolicy)
    timeout=30.0,             # Optional: step timeout in seconds
    on_error=ErrorPolicy(retry=2, on_failure=RecoveryAction.SKIP),  # Optional: per-step error policy
)
```

**Step types:**
1. **LLM step**: `model="gpt-4"` + `prompt="..."` — calls the specified model
2. **Tool step**: `tool="tool_name"` + `input={...}` — invokes a tool
3. **Hybrid step**: both `model` and `tool` — tool with model fallback

**Step results are available in context** as `{step_name}` in subsequent prompts:
```python
Step("search", model="gpt-4", prompt="Find info about {topic}")
Step("summarize", model="gpt-4", prompt="Summarize: {search}")  # {search} = search step's output
```

### Parallel

```python
from loopmaster import Parallel, Step

Parallel(
    Step("task_a", model="gpt-4", prompt="Subtask A"),
    Step("task_b", model="gpt-4", prompt="Subtask B"),
    Step("task_c", model="gpt-4", prompt="Subtask C"),
)
```

Parallel steps execute concurrently. Results are merged into context after all complete.

### Context

```python
from loopmaster import Context

# Context is passed between steps
# Access via {variable_name} in prompt templates
# Step outputs are automatically merged into context

@Loop(name="pipeline")
def pipeline(ctx):
    Step("step1", model="gpt-4", prompt="Process: {input}")
    # step1's output is available as {step1}
    Step("step2", model="gpt-4", prompt="Refine: {step1}")
    return ctx
```

Context is **immutable per-step** (snapshot pattern). Changes from one step don't affect others until explicitly merged.

### LoopEngine

```python
from loopmaster import LoopEngine, Budget, ErrorPolicy, RecoveryAction

engine = LoopEngine(
    budget=Budget(max_cost=5.00, max_tokens=100000, max_steps=20),
    error_policy=ErrorPolicy(
        retry=3,
        backoff=1.0,
        on_failure=RecoveryAction.RETRY,
        fallback_model="gpt-4o-mini",
    ),
    checkpoint_dir="./checkpoints",
)
```

---

## CLI Commands

| Command | Description | Example |
|---------|-------------|---------|
| `loop-engine init NAME` | Create loop file from default template | `loop-engine init my-loop` |
| `loop-engine init NAME -t TEMPLATE` | Use specific template | `loop-engine init my-loop -t reflection` |
| `loop-engine init NAME --task "desc"` | Custom task description | `loop-engine init my-loop --task "analyze code"` |
| `loop-engine validate FILE` | Validate without execution | `loop-engine validate my_loop.py` |
| `loop-engine run FILE` | Execute loop | `loop-engine run my_loop.py` |
| `loop-engine run FILE -r` | Resume from checkpoint | `loop-engine run my_loop.py -r` |
| `loop-engine run FILE -d` | Dry run (no LLM calls) | `loop-engine run my_loop.py -d` |
| `loop-engine checkpoints NAME` | List checkpoints | `loop-engine checkpoints my-loop` |
| `loop-engine templates` | List available templates | `loop-engine templates` |
| `loop-engine export FILE` | Export to YAML | `loop-engine export my_loop.py` |
| `loop-engine export FILE --format json` | Compile to LoopSpec v1 JSON | `loop-engine export my_loop.py --format json -o loop.json` |
| `loop-engine block add NAME VER` | Register code block (DB, SHA-256 pinned) | `loop-engine block add my-block 1.0.0 --lang python --source block.py` |
| `loop-engine block get REF` | Get block metadata + source | `loop-engine block get my-block@1.0.0` |
| `loop-engine block list [PATTERN]` | List registered blocks | `loop-engine block list` |
| `loop-engine docs` | Open documentation | `loop-engine docs` |

**Output of `run`:**
- Cost: `$0.0420`
- Tokens: `1234`
- Steps: `search, summarize, critique`

---

## MCP Tools — OpenCode as Provider

LoopMaster exposes **17 MCP tools** in 5 groups. Two execution models exist:

- **Detached** (`loop_run` with `mode="detached"` or JSON `loop_save` → `loop_run`): engine runs the loop in a daemon thread inside the MCP process (shell/code/human/http/mcp executors). You poll `loop_status` until `completed`/`failed`/`waiting_input`. Checkpoints are written to `.loopmaster/checkpoints`, heartbeats keep `updated_at` fresh, stale/owner-dead detection reaps zombies.
- **Agent** (`loop_run` with `mode="agent"` or legacy `loop_get` → `loop_result`): **OpenCode IS the LLM provider** — it reads the spec and walks the steps itself, calling `loop_record` per leaf. `finalize=true` on the last record of a `Conditional` branch is required (both branches count toward `total_steps`). All responses carry `pending_notifications`.

### Detached execution

```
User → "Run the shell pipeline (JSON)"
  ↓
OpenCode: loop_save(name, spec_json) → loop_run(spec_json, mode="detached")
  ↓
Engine thread: ShellExecutor / CodeBlockExecutor / HumanInputExecutor / …
  ↓
OpenCode polls loop_status(job_id) → {status, progress, results, error, metrics}
  → on waiting_input also {question}, on error also {error}
```

### Agent execution (legacy + JSON)

```
User → "Run the research loop"
  ↓
OpenCode: loop_get("research")  [DSL]  or  loop_run(spec_json, mode="agent") [JSON]
  ↓
OpenCode executes Step 1 with its own model → loop_record(job_id, "search", success=True, output="...")
  ↓
… repeat per leaf step, finalize=true on last branch step …
  ↓
Loop complete (status completed). OpenCode reports summary.
```

### Available Tools (17)

| Group | Tool | Description | Key Input |
|-------|------|-------------|-----------|
| Loops | `loop_list` | Discover DSL loops on disk | `{search_dir?}` |
| Loops | `loop_get` | Get full DSL definition + job_id (agent walk) | `{loop_name, search_dir?}` |
| Loops | `loop_save` | Persist a JSON LoopSpec v1 | `{loop_name, spec_json}` |
| Loops | `loop_delete` | Delete a persisted JSON loop | `{loop_name}` |
| Loops | `loop_run` | Start a JSON loop (`mode`: `detached`\|`agent`, `spec_json`, `context`) | `{spec_json?, loop_name?, mode?, context?}` |
| Loops | `loop_status` | Poll progress + results + question if waiting | `{job_id}` |
| Loops | `loop_cancel` | Cancel a running/detached job | `{job_id}` |
| Loops | `loop_record` | Record a JSON-loop step (agent mode, `finalize?`) | `{job_id, step_name, success?, output?, error?, finalize?}` |
| Loops | `loop_result` | **Legacy** DSL step report (use `loop_record` for JSON) | `{job_id, step_name, success, output?, error?}` |
| Blocks | `block_add` | Register versioned code block (SHA-256 pinned) | `{name, version, language, source, capabilities?, description?}` |
| Blocks | `block_get` | Fetch block metadata + source | `{ref}` |
| Blocks | `block_list` | List blocks | `{pattern?}` |
| HITL | `loop_questions` | List pending questions | `{job_id?}` |
| HITL | `loop_respond` | Answer a question (`job_id` validated) | `{job_id, msg_id, answer}` |
| Notify | `loop_inbox` | Poll notifications (`info`/`needs_input`/`critical`), auto-marks read | `{unread_only?, limit?, mark_read?}` |
| Models | `model_list` | List registered models + pricing | `{}` |
| Models | `model_recommend` | Recommend model for task/budget | `{task, prompt_tokens?, remaining_budget?}` |

### Tool Details

#### `loop_list`

Scans for `@Loop` files and returns available loops.

```
loop_list()
→ "Found 3 loop(s):
    - research v1.0.0 (4 steps) [research_loop.py]
    - code-review v2.0.0 (5 steps) [review.py]
    - pipeline v1.0.0 (3 steps) [pipeline.py]"
```

#### `loop_get`

Returns the FULL loop definition for OpenCode to execute. Includes:
- All steps with prompts, models, tools, error policies
- Budget constraints
- Instructions for execution

```
loop_get(loop_name="research")
→ {
    "job_id": "research_1692000000",
    "loop": {
      "name": "research",
      "steps": [
        {"name": "search", "model": "gpt-4", "prompt": "Research: {topic}"},
        {"name": "analyze", "model": "gpt-4", "prompt": "Analyze: {search}"},
        {"name": "summarize", "model": "gpt-4", "prompt": "Summarize: {analyze}"}
      ]
    },
    "instructions": "Execute each step in order..."
  }
```

#### `loop_result`

OpenCode calls this after executing each step. Returns the next step to execute or completion status.

```
loop_result(job_id="research_1692000000", step_name="search", success=True, output="Found 5 articles about...")
→ {
    "status": "in_progress",
    "progress": "1/3",
    "next_step": {"name": "analyze", "model": "gpt-4", "prompt": "Analyze: {search}"}
  }
```

#### `loop_status`

Check current progress of a loop execution.

#### `loop_cancel`

Cancel a running loop.

### Error Recovery via MCP

When a step fails, `loop_result` returns recovery suggestions based on the step's error policy:

```
loop_result(job_id="...", step_name="fetch", success=False, error="Rate limit exceeded")
→ {
    "status": "error",
    "step": "fetch",
    "progress": "1/4",
    "error": "Rate limit exceeded",
    "suggestion": "Retry the step (up to 3 times)."
  }
```

OpenCode should follow the suggestion and retry/skip/abort as indicated.

### MCP Configuration

The LoopMaster MCP server is configured in `opencode.json`:

```json
"loopmaster": {
  "type": "local",
  "command": ["python", "C:/Projects/Ideas/LoopMaster/scripts/loopmaster_mcp.py"],
  "enabled": true,
  "cwd": "C:/Projects/Ideas/LoopMaster"
}
```

### Loop File Discovery

The MCP server scans these directories for `@Loop` files:
1. Current working directory (`cwd` from config)
2. `LOOPMASTER_LOOPS_DIR` environment variable (if set)

Any `.py` file containing `@Loop` or `from loopmaster` is considered a loop file.

---

## Creating Custom Loops

### Basic pattern

```python
"""My custom loop."""

from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="my-custom-loop", version="0.1.0")
def my_custom_loop(ctx):
    Step("step_1", model="gpt-4", prompt="First step with {input_data}")
    Step("step_2", model="gpt-4", prompt="Second step using {step_1}")
    Step("step_3", model="gpt-4", prompt="Final step: {step_2}")
    return ctx
```

### Loop with error recovery

```python
from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="resilient-loop", version="0.1.0")
def resilient_loop(ctx):
    Step(
        "risky_operation",
        model="gpt-4",
        prompt="Do something that might fail",
        retry=3,  # Per-step retry override
        on_error=ErrorPolicy(
            retry=5,
            backoff=2.0,
            on_failure=RecoveryAction.SKIP,
        ),
    )
    return ctx
```

### Loop with budget

```python
from loopmaster import Loop, Step, Budget

@Loop(name="budgeted-loop", version="0.1.0", budget="$2.00")
def budgeted_loop(ctx):
    Step("step_1", model="gpt-4", prompt="Work within budget")
    return ctx
```

Or programmatically:
```python
engine = LoopEngine(budget=Budget(max_cost=2.00, max_tokens=50000))
```

### Loop with parallel steps

```python
from loopmaster import Loop, Step, Parallel

@Loop(name="parallel-loop", version="0.1.0")
def parallel_loop(ctx):
    # These 3 steps execute concurrently
    Parallel(
        Step("research", model="gpt-4", prompt="Research {topic}"),
        Step("outline", model="gpt-4", prompt="Create outline for {topic}"),
        Step("references", model="gpt-4", prompt="Find references for {topic}"),
    )
    # This step waits for all parallel steps to complete
    Step("merge", model="gpt-4", prompt="Merge: {research}, {outline}, {references}")
    return ctx
```

### Loop with tools

```python
from loopmaster import Loop, Step

@Loop(name="tool-loop", version="0.1.0")
def tool_loop(ctx):
    Step("search", tool="web_search", input={"query": "{search_query}"})
    Step("analyze", model="gpt-4", prompt="Analyze search results: {search}")
    Step("write", tool="file_write", input={"path": "output.md", "content": "{analyze}"})
    return ctx
```

### Loop with context manipulation

```python
from loopmaster import Loop, Step

@Loop(name="context-loop", version="0.1.0")
def context_loop(ctx):
    Step("init", model="gpt-4", prompt="Set up initial context")
    # Step output is available as {step_name} in next prompt
    Step("process", model="gpt-4", prompt="Process: {init}")
    # Access multiple step outputs
    Step("final", model="gpt-4", prompt="Combine {init} and {process}")
    return ctx
```

### Loop with escalation (try cheap, then expensive)

```python
from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="escalation-loop", version="0.1.0")
def escalation_loop(ctx):
    Step(
        "try_cheap",
        model="gpt-4o-mini",
        prompt="Solve: {problem}",
        on_error=ErrorPolicy(
            retry=1,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model="gpt-4",
        ),
    )
    return ctx
```

### Loop with custom engine configuration

```python
from loopmaster import Loop, Step, LoopEngine, ErrorPolicy, RecoveryAction, InterruptionProtection

@Loop(name="full-config-loop", version="0.1.0")
def full_config_loop(ctx):
    Step("step1", model="gpt-4", prompt="Do work")
    return ctx

# Configure engine with all options
engine = LoopEngine(
    error_policy=ErrorPolicy(
        retry=3,
        backoff=1.5,
        on_failure=RecoveryAction.RETRY,
        fallback_model="gpt-4o-mini",
    ),
    interruption_protection=InterruptionProtection(
        enabled=True,
        heartbeat_interval=30.0,
        heartbeat_timeout=60.0,
        pre_step_checkpoint=True,
        post_step_checkpoint=True,
    ),
    checkpoint_dir="./checkpoints",
)

result = engine.run(full_config_loop._loop_def, {"problem": "optimize code"})
print(f"Success: {result.success}, Cost: ${result.total_cost:.4f}")
```

---

## Error Recovery

### RecoveryAction enum

| Action | Behavior |
|--------|----------|
| `ABORT` | Stop the loop immediately (default) |
| `SKIP` | Mark step as failed, continue with next |
| `RETRY` | Re-run the step (up to `retry` times) |
| `FALLBACK` | Switch to `fallback_model` and retry once |

### ErrorPolicy

```python
from loopmaster import ErrorPolicy, RecoveryAction

policy = ErrorPolicy(
    retry=3,                    # Max retries per step
    backoff=1.0,                # Seconds between retries
    on_failure=RecoveryAction.RETRY,  # What to do after retries exhausted
    fallback_model="gpt-4o-mini",     # Used with FALLBACK action
)
```

### Error classification

Errors are classified automatically:
- `RateLimitError`, `TimeoutError` → `RETRY`
- `ValidationError`, `SchemaError` → `SKIP`
- Everything else → `on_failure` policy

### Per-step error policy

```python
Step(
    "critical_step",
    model="gpt-4",
    prompt="Important task",
    retry=5,  # Overrides ErrorPolicy.retry for this step
    on_error=ErrorPolicy(retry=3, on_failure=RecoveryAction.ABORT),
)
```

### Exception hierarchy

```
LoopError (base)
├── StepError          — step execution failed
├── CheckpointError    — checkpoint save/load failed
├── BudgetExceededError — budget limit exceeded
├── InterruptedError   — loop was interrupted
└── ReplayError        — deterministic replay failed
```

---

## Budget Control

### Budget types

```python
from loopmaster import Budget

# Cost budget (USD)
Budget(max_cost=5.00)

# Token budget
Budget(max_tokens=100000)

# Step count budget
Budget(max_steps=20)

# Combined
Budget(max_cost=5.00, max_tokens=100000, max_steps=20)

# From string
Budget.from_string("$5.00")  # → Budget(max_cost=5.00)
```

### Budget enforcement

Budget is checked **before each step**. When exceeded, `BudgetExceededError` is raised.

```python
engine = LoopEngine(budget=Budget(max_cost=2.00))
result = engine.run(loop_def, {})
# If total cost exceeds $2.00, loop stops
```

---

## Checkpoints & Resume

### Automatic checkpoints

Checkpoints are saved automatically after each step (or based on `InterruptionProtection` config).

```python
engine = LoopEngine(checkpoint_dir="./checkpoints")
result = engine.run(loop_def, {"name": "world"})
# Checkpoints saved to ./checkpoints/my-loop_*.json
```

### Resume from checkpoint

```python
from loopmaster.checkpoint import CheckpointManager

mgr = CheckpointManager("./checkpoints")
checkpoint = mgr.load_latest("my-loop")
result = engine.run(loop_def, {}, resume_checkpoint=checkpoint)
```

### CLI resume

```bash
loop-engine run my_loop.py --resume
```

### Checkpoint data

Checkpoints store:
- Loop name and version
- Executed step names
- Step results
- Context snapshot
- Resume count

---

## Context Passing

### Initial context

```python
result = engine.run(loop_def, {"topic": "quantum computing", "depth": 3})
```

### Step output in context

Each step's output is automatically merged into context under its step name:
```python
Step("search", model="gpt-4", prompt="Find {topic}")
# After execution, {search} = search step's output

Step("summarize", model="gpt-4", prompt="Summarize: {search}")
```

### Context snapshot pattern

Context is **immutable per-step**:
- Each step receives a snapshot of the current context
- Changes from one step don't affect others until explicitly merged
- This prevents race conditions in parallel execution

---

## Templates

### Available templates

| Template | Description |
|----------|-------------|
| `reflection` | Execute → evaluate → revise → repeat |
| `tool_use` | Agent calls tools in a loop |
| `planning` | Plan → execute → verify → replan |
| `multi_agent` | Multiple agents work in parallel |
| `critique` | Generate → critique → revise cycle |
| `escalation` | Try cheap model first, escalate on failure |
| `hybrid` | Combination of reflection + tool_use + planning |

### Generate from template

```bash
# CLI
loop-engine init my-loop -t reflection --task "analyze code"

# Python
from loopmaster.templates import generate_code
code = generate_code("reflection", name_var="my_loop", task="analyze code")
```

### List templates

```bash
loop-engine templates
```

---

## Usage Scenarios

### Scenario 1: Code review loop

```python
from loopmaster import Loop, Step, Parallel

@Loop(name="code-review", version="1.0.0")
def code_review(ctx):
    Parallel(
        Step("security", model="gpt-4", prompt="Security review of {code}"),
        Step("performance", model="gpt-4", prompt="Performance review of {code}"),
        Step("style", model="gpt-4", prompt="Style review of {code}"),
    )
    Step("merge_reviews", model="gpt-4", prompt="Merge: {security}, {performance}, {style}")
    return ctx
```

### Scenario 2: Research with budget

```python
from loopmaster import Loop, Step, Budget

@Loop(name="research", version="1.0.0", budget="$3.00")
def research_loop(ctx):
    Step("search", model="gpt-4", prompt="Research: {question}")
    Step("analyze", model="gpt-4", prompt="Analyze: {search}")
    Step("synthesize", model="gpt-4", prompt="Synthesize: {analyze}")
    return ctx
```

### Scenario 3: Resilient pipeline

```python
from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="resilient-pipeline", version="1.0.0")
def resilient_pipeline(ctx):
    Step(
        "fetch",
        model="gpt-4",
        prompt="Fetch data from {source}",
        retry=3,
        on_error=ErrorPolicy(retry=5, on_failure=RecoveryAction.SKIP),
    )
    Step("process", model="gpt-4", prompt="Process: {fetch}")
    return ctx
```

### Scenario 4: Multi-agent collaboration

```python
from loopmaster import Loop, Step, Parallel

@Loop(name="multi-agent", version="1.0.0")
def multi_agent(ctx):
    Parallel(
        Step("researcher", model="gpt-4", prompt="Research: {topic}"),
        Step("writer", model="gpt-4", prompt="Draft content: {topic}"),
        Step("reviewer", model="gpt-4", prompt="Review plan: {topic}"),
    )
    Step("coordinator", model="gpt-4", prompt="Coordinate: {researcher}, {writer}, {reviewer}")
    return ctx
```

### Scenario 5: Escalation pattern

```python
from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="escalation", version="1.0.0")
def escalation_loop(ctx):
    Step(
        "try_fast",
        model="gpt-4o-mini",
        prompt="Quick attempt: {task}",
        on_error=ErrorPolicy(retry=1, on_failure=RecoveryAction.FALLBACK, fallback_model="gpt-4"),
    )
    return ctx
```

### Scenario 6: Tool orchestration

```python
from loopmaster import Loop, Step

@Loop(name="tool-orchestration", version="1.0.0")
def tool_orchestration(ctx):
    Step("search", tool="web_search", input={"query": "{query}"})
    Step("read", tool="web_read", input={"url": "{search}"})
    Step("summarize", model="gpt-4", prompt="Summarize: {read}")
    Step("write", tool="file_write", input={"path": "output.md", "content": "{summarize}"})
    return ctx
```

---

## Agent Adapter Integration

### OpenCode adapter

```python
from loopmaster.agents import AgentRegistry

registry = AgentRegistry()
adapter = registry.get_adapter("opencode")

# Discover agent
info = adapter.discover()
print(info.config_paths, info.prompt_paths)

# Inject loop instructions
adapter.inject_loop_context("Execute this loop: ...")

# Restore after loop
adapter.restore_original()
```

### Config files

| Agent | Config | Prompt |
|-------|--------|--------|
| OpenCode | `~/.config/opencode/opencode.json` | `~/.config/opencode/agents/*.md` |
| Claude Code | `~/.claude/settings.json` | `~/.claude/CLAUDE.md` |
| Cursor | `.cursorrules`, `.cursor/` | `.cursor/rules` |
| Custom | User-specified | User-specified |

### Prompt injection

LoopMaster injects instructions via HTML comment markers:
```html
<!-- LOOP_ENGINEER:start -->
Your loop instructions here
<!-- LOOP_ENGINEER:end -->
```

The `PromptManager` handles injection/removal without overwriting existing content.

---

## Metrics & Cost Tracking

### MetricsCollector

```python
from loopmaster.metrics import MetricsCollector

collector = MetricsCollector("./metrics")
engine = LoopEngine(metrics_collector=collector)

result = engine.run(loop_def, {})

# Save metrics
collector.save("./metrics/data.json")

# Load metrics
collector.load("./metrics/data.json")

# Export to SQLite
from loopmaster.metrics.sqlite_exporter import SQLiteExporter
with SQLiteExporter("./metrics.db") as exp:
    exp.export_collector(collector)
```

### CostTracker

```python
from loopmaster.cost import CostTracker

tracker = CostTracker()
engine = LoopEngine(cost_tracker=tracker)

result = engine.run(loop_def, {})

# Get total cost
print(f"Total cost: ${tracker.total_cost:.4f}")

# Save cost data
tracker.save("./checkpoints/costs.json")
```

---

## Common Patterns

### Pattern: Sequential pipeline

```python
@Loop(name="pipeline")
def pipeline(ctx):
    Step("step1", model="gpt-4", prompt="Process: {input}")
    Step("step2", model="gpt-4", prompt="Refine: {step1}")
    Step("step3", model="gpt-4", prompt="Finalize: {step2}")
    return ctx
```

### Pattern: Fan-out / Fan-in

```python
@Loop(name="fan-out")
def fan_out(ctx):
    Parallel(
        Step("worker1", model="gpt-4", prompt="Task 1: {input}"),
        Step("worker2", model="gpt-4", prompt="Task 2: {input}"),
        Step("worker3", model="gpt-4", prompt="Task 3: {input}"),
    )
    Step("aggregator", model="gpt-4", prompt="Combine: {worker1}, {worker2}, {worker3}")
    return ctx
```

### Pattern: Retry with fallback

```python
@Loop(name="retry-fallback")
def retry_fallback(ctx):
    Step(
        "attempt",
        model="gpt-4o-mini",
        prompt="Try cheap approach: {task}",
        on_error=ErrorPolicy(retry=2, on_failure=RecoveryAction.FALLBACK, fallback_model="gpt-4"),
    )
    return ctx
```

### Pattern: Validation loop

```python
@Loop(name="validate")
def validate(ctx):
    Step("generate", model="gpt-4", prompt="Generate: {spec}")
    Step("validate", model="gpt-4", prompt="Validate: {generate}")
    Step("fix", model="gpt-4", prompt="Fix issues: {validate}")
    return ctx
```

---

## Troubleshooting

### Common errors

**`No @Loop found`**
- Ensure your file has a `@Loop` decorated function
- The function must have `_loop_def` attribute (set by decorator)

**`Budget exceeded: $0.0000 / $5.0000`**
- Check your `Budget` configuration
- Cost tracking requires `CostTracker` to be attached to engine

**`Step 'name' failed: ...`**
- Check step configuration (model, prompt, tool)
- Verify context variables exist: `{variable}` in prompt

**`Checkpoint save failed`**
- Check `checkpoint_dir` permissions
- Verify disk space

### Debug mode

```python
import logging
logging.basicConfig(level=logging.DEBUG)

engine = LoopEngine()
# Detailed logs for step execution, checkpoints, etc.
```

### Dry run

```bash
loop-engine run my_loop.py --dry-run
```

### Export to YAML for inspection

```bash
loop-engine export my_loop.py
```

---

## API Reference

### Core exports

```python
from loopmaster import (
    Loop,           # @Loop decorator
    Step,           # Step dataclass
    Parallel,       # Parallel execution wrapper
    Context,        # Mutable context object
    LoopEngine,     # Main execution engine
    Budget,         # Budget constraints
    ErrorPolicy,    # Error handling policy
    RecoveryAction, # ABORT, SKIP, RETRY, FALLBACK
    InterruptionProtection,  # Heartbeat & checkpoint config
    StepInput,      # Immutable step input
    StepOutput,     # Step output diff
    StepResult,     # Step execution result
    LoopError,      # Base exception
    StepError,      # Step failure
    CheckpointError, # Checkpoint failure
    BudgetExceededError,  # Budget exceeded
    InterruptedError,     # Loop interrupted
)
```

### LoopEngine

```python
engine = LoopEngine(
    budget=Budget(...),
    error_policy=ErrorPolicy(...),
    interruption_protection=InterruptionProtection(...),
    checkpoint_dir="./checkpoints",
    metrics_collector=MetricsCollector(...),
    cost_tracker=CostTracker(...),
)

result = engine.run(loop_def, initial_context, resume_checkpoint=None)
# result.success: bool
# result.error: str | None
# result.total_cost: float
# result.total_tokens: int
# result.steps_executed: list[str]
# result.checkpoint_saved: bool
# result.last_checkpoint: CheckpointData | None
```

### LoopRunResult

```python
@dataclass
class LoopRunResult:
    success: bool
    results: dict[str, StepResult]
    total_cost: float
    total_tokens: int
    steps_executed: list[str]
    error: str | None
    interrupted: bool
    resume_count: int
    last_checkpoint: CheckpointData | None
    checkpoint_saved: bool
```

---

*Generated for LoopMaster v0.1.0 — 263 tests, aislop 100/100*
