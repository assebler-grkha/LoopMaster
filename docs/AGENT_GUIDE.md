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

## 2. Model Discovery via MCP Server

When designing or configuring loops dynamically, query available and approved models via MCP tools:

### `model_list`
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

### `model_recommend`
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

## 3. Best Practices for Error Policies & Fallbacks

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

## 4. Single-Step & Loop Budget Safety

1. Always set a `Budget(max_cost=..., max_tokens=...)` for loops that execute in autonomous environments.
2. In production, configure `ModelPolicy(max_cost_per_step=0.25)` to prevent accidental huge-context queries.
