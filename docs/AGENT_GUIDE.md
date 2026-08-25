# LoopMaster Agent Guide: Model Selection & Loop Design

This guide provides guidelines for AI coding assistants (Claude Code, Cursor, OpenCode, Antigravity) when generating, optimizing, or executing LoopMaster loops.

---

## 1. Golden Rule: Use Semantic Aliases

**Do NOT hardcode proprietary model strings** (e.g. `model="gpt-4o"` or `model="claude-3-5-sonnet"`) in loop definitions unless specifically requested by the user.

Always prefer **semantic aliases**:

| Semantic Alias | Recommended For | Default Resolved Model | Cost Tier |
|---|---|---|---|
| `@fast` | Classification, extraction, summarization, routing, simple JSON formatting | `gpt-4o-mini` / `gemini-1.5-flash` | Ultra-low (< $0.15/1M) |
| `@smart` | Complex reasoning, architecture design, multi-step logic, edge-case analysis | `gpt-4o` / `gemini-1.5-pro` | Standard ($2.50/1M) |
| `@coding` | Code generation, complex refactoring, bug fixes, test generation | `claude-3-5-sonnet` | Standard ($3.00/1M) |
| `@cheap` | Background polling, log parsing, large batch iterations | `gemini-1.5-flash` / `gpt-4o-mini` | Minimum |
| `@reasoning` | Deep algorithmic / mathematical problem solving | `o1-mini` | High ($3.00/1M) |
| `@fallback` | Resilient error recovery model for fallback policies | `gpt-4o-mini` | Ultra-low |
| `@auto` | Dynamically auto-routed based on prompt size & task complexity | Auto-selected | Adaptive |

### Example
```python
from loopmaster import Loop, Step

@Loop(name="clean-architecture", version="1.0.0")
def architecture_loop(ctx):
    # Fast initial classification
    Step("classify", model="@fast", prompt="Classify request: {task_desc}")

    # High-capability coding step
    Step("generate_code", model="@coding", prompt="Write production code for: {classify}")

    # Fast validation
    Step("summarize", model="@fast", prompt="Summarize changes: {generate_code}")
    return ctx
```

---

## 2. Proactive Loop Triggering

**You MUST proactively suggest LoopMaster loops when you recognize a suitable task pattern.** Do not wait for the user to ask about loops.

### Trigger Patterns

| Pattern | Signal | Action |
|---------|--------|--------|
| **Multi-aspect analysis** | Task touches 3+ independent dimensions (security, perf, style) | Suggest `Parallel` loop |
| **Iterative refinement** | "improve", "optimize", "review and fix" | Suggest `reflection` loop |
| **Research + synthesis** | "research", "investigate", "compare options" | Suggest `research` loop |
| **Test generation** | "write tests", "add coverage" | Suggest `test-generation` loop |
| **Budget constraint** | "cheap", "under $X", "minimal cost" | Add `Budget(max_cost=...)` |
| **Error-prone operation** | External API calls, network requests | Add `ErrorPolicy` with fallback |

### Anti-Triggers (Do NOT suggest loops for)

- Single-step tasks ("fix this typo", "what is X")
- Tasks requiring real-time user interaction
- Questions that are faster to answer directly

### Suggestion Template

When triggering, use this pattern:

```
I notice this task fits a loop pattern: [brief description].
Suggested loop: [loop_name] ([N] steps: [step list])
Estimated cost: ~$[X.XX]
Want me to run it?
```

### Confirmation Required

**Always ask for user confirmation before running a loop.** Exception: if the user explicitly said "just do it" or "use a loop" in their message.

---

## 3. Model Discovery & Loop Execution via MCP Server

LoopMaster exposes **17 MCP tools** in 5 groups. All are available when the MCP server is running.

### 3.1 Loops — discovery & execution

| Tool | Purpose |
|------|---------|
| `loop_list` | Discover available loops (Python DSL files) |
| `loop_get` | Get full DSL definition + job_id for agent execution |
| `loop_save` | Persist a JSON LoopSpec (LoopSpec v1) to the store |
| `loop_delete` | Remove a persisted JSON loop |
| `loop_run` | Start a loop: `mode="detached"` (engine runs it) or `mode="agent"` (you drive it) |
| `loop_status` | Poll progress, results, stale/owner-dead detection |
| `loop_cancel` | Cancel a running/detached job |
| `loop_result` | **Legacy** — report a DSL step result (use `loop_record` for JSON loops) |
| `loop_record` | Report a JSON-loop step result (`finalize=true` for conditional branches) |

**Execution models:**

- **Detached** (`mode="detached"`, default for JSON): engine runs `ShellExecutor`/`CodeBlockExecutor`/`HumanInputExecutor`/`HttpExecutor` in a daemon thread inside the MCP process. You poll `loop_status` until `completed`/`failed`/`waiting_input`.
- **Agent** (`mode="agent"`): engine creates a `ready` job and does nothing. You walk the steps yourself (LLM calls are your model) and call `loop_record(job_id, step_name, success, output, error, finalize?)` for each leaf. `finalize=true` on the last record of a `Conditional` branch (otherwise `total_steps` counts both branches and auto-complete never fires).

### 3.2 HITL — human-in-the-loop

| Tool | Purpose |
|------|---------|
| `loop_questions` | List pending questions (`waiting_input` jobs) |
| `loop_respond` | Answer a question (`job_id` is validated against `msg.job_id`) |

Flow: JSON loop reaches a `human` node → job goes `waiting_input`, `loop_status` attaches the question, notification `needs_input/waiting_input` is emitted → you call `loop_respond`; on timeout `on_timeout` policy fires (`default_answer`/`skip`/`fail`/`escalate`→critical notification).

### 3.3 Code Blocks — reusable code

| Tool | Purpose |
|------|---------|
| `block_add` | Register a versioned code block (`name@version`, `language: python|shell`, SHA-256 pinned) |
| `block_get` | Fetch block metadata + source + `verified_sha256` |
| `block_list` | List blocks (optional LIKE pattern) |

Blocks run `subprocess-only` via `CodeBlockExecutor` with streaming 1 MiB stdout limit, minimal env, and `deny_capabilities` enforcement at both save-time and exec-time.

### 3.4 Notifications — outbox

| Tool | Purpose |
|------|---------|
| `loop_inbox` | Poll unread notifications (`info`/`needs_input`/`critical`), auto-marks read; also sweeps expired messages to `archive/messages-YYYYMM.jsonl` |

Every tool response carries `pending_notifications: {info, needs_input, critical}`. Critical notifications also mirror to `.loopmaster/inbox/critical.json`.

### 3.5 Models — discovery

#### `model_list`
Lists all registered models, their active API key statuses, context windows, and token pricing.
```json
// MCP Tool Call: model_list
// Response includes:
{
  "count": 9,
  "approved_count": 9,
  "models": [
    {
      "name": "gpt-4o-mini",
      "provider": "openai",
      "aliases": ["@fast", "@cheap", "@default", "@fallback"],
      "cost_input_1m": 0.15,
      "cost_output_1m": 0.60,
      "context_window": 128000,
      "approved": true,
      "is_available": true
    }
  ]
}
```

#### `model_recommend`
Ask LoopMaster to recommend the optimal model for a specific task and token budget:
```json
// MCP Tool Call: model_recommend
// Arguments: {"task": "Write complex rust parser", "prompt_tokens": 1500, "remaining_budget": 1.00}
// Response:
{
  "recommended_model": "claude-3-5-sonnet",
  "aliases": ["@coding", "@smart_anthropic"],
  "reason": "Code generation / review task matches coding tier model",
  "estimated_cost": 0.0075
}
```

---

## 4. Best Practices for Error Policies & Fallbacks

Always configure fallback models using semantic aliases so loops can recover gracefully from provider outages:

```python
from loopmaster import ErrorPolicy, RecoveryAction

# Resilient error policy
safe_policy = ErrorPolicy(
    retry=3,
    backoff=1.5,
    on_failure=RecoveryAction.FALLBACK,
    fallback_model="@fallback", # Automatically routes to approved fallback model
)
```

---

## 5. Single-Step & Loop Budget Safety

1. Always set a `Budget(max_cost=..., max_tokens=...)` for loops that execute in autonomous environments.
2. In production, configure `ModelPolicy(max_cost_per_step=0.25)` to prevent accidental huge-context queries.
