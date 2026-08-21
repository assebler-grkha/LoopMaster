# LoopMaster — DSL Specification

## Overview

LoopMaster uses a Python DSL with decorators and runtime calls. The pattern is similar to FastAPI: declarative API over imperative language.

## Core Constructs

### @Loop Decorator

```python
from loopmaster import Loop, Step, Parallel

@Loop(
    name="research",                    # Required: unique loop identifier
    version="1.0.0",                    # Optional: semver for checkpoint versioning
    agent="opencode",                   # Optional: target agent type
    budget=Budget(max_cost="$5.00"),    # Optional: budget constraints
    interruption_protection=InterruptionProtection(  # Optional
        enabled=True,
        heartbeat_interval=30,
        heartbeat_timeout=60,
    ),
)
def research_loop(ctx):
    """Loop body — Python code, not YAML."""
    Step("search", tool="web_search", input=ctx.query)
    Step("analyze", model="gpt-4", prompt="Analyze: {search_results}")

    if ctx.analyze.confidence < 0.7:
        Step("deep_dive", model="gpt-4", prompt="Deep analysis: {topic}")

    return Step("synthesize", model="gpt-4", prompt="Synthesize findings")
```

### Step() Runtime Call

`Step()` is a **runtime call** inside the loop body, NOT a decorator. The loop runtime wraps the loop body and intercepts `Step()` calls during execution.

**Execution model (lazy execution):**

1. Runtime wraps the loop body in an executor
2. When the body calls `Step("search", ...)`, the runtime:
   - Freezes the current context as an immutable snapshot
   - Passes the snapshot to the step
   - Step executes (LLM call / tool call)
   - Step returns a `StepOutput` (diff)
   - Runtime merges the diff into the context
   - Runtime records the step name in `executed_step_names`
3. The body continues executing — Python control flow (if/for/while) evaluates normally
4. The runtime tracks which step names were executed for checkpointing

**Key property:** Step() blocks until completion. The runtime does NOT need two-phase collection — it executes the body directly, and Step() calls register themselves as they run. Dynamic control flow (conditionals, loops) works naturally because Python evaluates it at runtime.

```python
Step(
    name="search",              # Required: step identifier
    tool="web_search",          # Either tool OR model (not both)
    model="gpt-4",              # Model for LLM call
    prompt="Search for: {query}",  # Prompt template (references context)
    input=data,                 # Raw input (alternative to prompt)
    retry=3,                    # Optional: retry count
    timeout=30,                 # Optional: timeout in seconds
    on_error=ErrorPolicy(retry=2, backoff=2.0),  # Optional: per-step error policy
)
```

**Result storage:** Each step auto-stores its result under its step name in the context. After `Step("search", ...)`, the result is available as `ctx.search`.

### Parallel() for Concurrent Steps

```python
Parallel(
    Step("search_web", tool="web_search", input=ctx.query),
    Step("search_docs", tool="doc_search", input=ctx.query),
    # Both execute concurrently via the custom supervisor
    # Results stored as ctx.search_web and ctx.search_docs
)
```

### Native Python Control Flow

```python
@Loop(name="adaptive")
def adaptive_loop(ctx):
    Step("classify", model="gpt-4-mini", prompt="Classify: {input}")

    if ctx.classify.complexity == "high":
        Step("analyze", model="gpt-4", prompt="Deep analysis: {input}")
        Step("verify", model="gpt-4", prompt="Verify: {analyze}")
    else:
        Step("quick", model="gpt-4-mini", prompt="Quick answer: {input}")

    for item in ctx.items:
        Step(f"process_{item.id}", model="gpt-4", input=item)

    return Step("final", model="gpt-4", prompt="Final: {context}")
```

### Loop Composition (Nested @Loops)

```python
@Loop(name="inner_analysis")
def analysis_loop(ctx):
    Step("parse", tool="parse_doc", input=ctx.document)
    Step("extract", model="gpt-4", prompt="Extract key points: {parse}")
    return Step("summarize", model="gpt-4", prompt="Summarize: {extract}")

@Loop(name="outer_research")
def research_loop(ctx):
    Step("search", tool="web_search", input=ctx.query)
    Step("analyze", loop=analysis_loop, input=ctx.search)  # Nested loop
    return Step("report", model="gpt-4", prompt="Report: {analyze}")
```

**Composition rules:**
- Nested loops are isolated (own context, own budget tracking)
- Parent passes input to child; child returns output to parent
- Budget is separate per loop (child has own budget OR shares parent's — configurable)

## Context Object (ctx)

The context is an **immutable snapshot** pattern:

1. Each step receives a frozen snapshot of the context
2. Step returns a `StepResult` with updates
3. Runtime merges updates into the context for the next step

```python
@dataclass(frozen=True)
class StepInput:
    """Immutable snapshot of context at step start."""
    query: str
    previous_results: dict[str, Any]
    metadata: dict[str, Any]

@dataclass
class StepOutput:
    """Diff that the step returns. Runtime merges into context."""
    updates: dict[str, Any]  # {"search_results": [...], "confidence": 0.8}
```

**Auto-storing:** Step results are automatically stored in context under the step name. After `Step("search", ...)`, the result is available as `ctx.search`.

**Size limits:** Context has configurable size limits to prevent unbounded growth:
- `max_messages`: oldest messages are summarized when exceeded
- `max_tool_results`: large results become file references
- `max_metadata`: arbitrary limit on metadata size

## Checkpointing

### Data-Only Checkpoints

```python
@dataclass
class Checkpoint:
    loop_name: str
    loop_version: str           # User-declared semver (e.g. "1.0.0") — for display/matching
    loop_source_hash: str       # Computed hash of the loop function source — for integrity verification
    executed_step_names: list[str]  # Step names already completed (replaces step_index)
    context_data: dict          # Serialized ctx as plain dict
    completed_results: dict     # Step name -> result (serialized)
    recorded_responses: dict    # For deterministic replay
    agent_prompt_section: str   # Injected prompt section
    timestamp: float
    interruption_reason: str    # Why checkpoint was created
```

### Checkpoint Creation

- **Pre-step:** Before each step executes (default) — records `executed_step_names` up to this point
- **Post-step:** After each step completes (default) — records `executed_step_names` including the completed step
- **Emergency:** On interruption detection — captures current `executed_step_names` snapshot

### Resume

On resume:
1. Load loop from source by `loop_name` + `loop_version`
2. Verify `loop_source_hash` matches (warn if different, don't block)
3. Inject checkpoint data into context
4. Re-execute loop body — runtime skips any step whose name is in `executed_step_names`
5. Resume proceeds from the first unexecuted step

**Why `executed_step_names` instead of `step_index`:**
With dynamic control flow (conditionals, loops), step execution order is non-linear. `step_index=5` may map to different steps depending on which branch was taken. `executed_step_names` is branch-independent — on resume, the runtime re-executes the body, Python evaluates the same branches (using the restored context), and the skip-list ensures already-completed steps are not re-run.

## Dry-Run Mode

```bash
loop-engine run --dry-run my_loop.py
```

Dry-run:
- Validates loop topology (no missing references, no broken steps)
- Validates tool schemas (Pydantic model is instantiable)
- Executes Python callbacks with mock inputs (if pure functions)
- Skips all LLM calls and network I/O
- Reports estimated cost based on expected token counts

## YAML Export

```python
loop.export_yaml()  # Returns YAML string
loop.export_yaml("my_loop.yaml")  # Writes to file
```

**Limitations (v1):**
- Linear step sequences only
- No conditionals, no loops, no nested @Loops
- Warns users when loop contains unexportable control flow

```python
if loop.has_dynamic_control_flow:
    raise UnsupportedExportError(
        "Loop contains if/for/while. YAML export supports linear sequences only."
    )
```

## ErrorPolicy

```python
from loopmaster import ErrorPolicy, RecoveryAction

@Loop(name="resilient")
def resilient_loop(ctx):
    Step(
        "risky_step",
        model="gpt-4",
        prompt="Do something risky",
        on_error=ErrorPolicy(
            retry=3,
            backoff=2.0,
            on_failure=RecoveryAction.SKIP,    # Skip on max retries
            fallback_model="gpt-4o-mini",       # Fallback model
        ),
    )
```

### Default Error Policies

| Error Type | Default Behavior |
|---|---|
| Tool execution failure | Retry 2x, then abort |
| Schema validation failure | Retry with stricter prompt, then abort |
| Rate limit | Exponential backoff, then abort |
| Timeout | Retry 1x, then skip |
| Budget exceeded | Abort immediately |

## Interruption Protection

```python
@Loop(
    name="long_running",
    interruption_protection=InterruptionProtection(
        enabled=True,
        heartbeat_interval=30,      # Agent pings every 30s
        heartbeat_timeout=60,       # Miss 2 heartbeats → interruption
        pre_step_checkpoint=True,   # Checkpoint before each step
        post_step_checkpoint=True,  # Checkpoint after each step
        context_overflow_strategy="compress_and_resume",  # What to do on overflow
        max_resume_attempts=3,      # Give up after 3 failed resumes
    ),
)
```

### Detection

- Heartbeat: agent sends ping every N seconds; master misses 2×N → interruption
- Step acknowledgment: each step requires ack; no ack within timeout → interruption
- Session state file: agent writes current executed_step_names; if stale → agent frozen

### Recovery Scenarios

**A — Agent returns manually:**
1. Agent sends resume with job_id
2. Master loads checkpoint
3. Verifies version match
4. Injects resume instruction into prompt with full context of completed steps

**B — Master restarted:**
1. Scans active checkpoints on startup
2. Offers resume options

**C — Context overflow:**
1. Master detects truncated response
2. Emergency checkpoint
3. Compresses previous steps into summary
4. Sends compressed context + continue instruction
