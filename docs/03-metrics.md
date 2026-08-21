# LoopMaster — Metrics Specification

## Overview

Metrics measure loop efficiency and enable optimization. Three storage tiers: in-memory (real-time), disk SQLite (post-run), external (Prometheus/PostHog) via collector pattern.

## Metric Definitions

### Loop Efficiency Metrics

| Metric | Description | Type |
|---|---|---|
| **Task Completion Rate** | % of loops that reach their goal | Gauge |
| **Steps to Completion** | P50/P95/P99 step count (averages lie) | Histogram |
| **Execution Time** | P50/P95/P99 wall-clock time | Histogram |
| **Usage Frequency** | How often this loop is invoked | Counter |
| **Resource Consumption** | Total tokens, API calls, tool invocations | Counter |
| **Cost per Run** | Total cost in USD per loop execution | Gauge |
| **Cost per Task** | Cost per meaningful unit of work | Gauge |

### Optimization Metrics

| Metric | Description | Type |
|---|---|---|
| **Guardrail Violation Rate** | How often loop hits limits (max cost, max steps, content filter) | Counter |
| **Escalation Rate** | How often loop escalates to human (autonomy measure) | Gauge |
| **Idempotency Violation Rate** | Did resume re-execute and produce different results? | Counter |
| **Agent Intervention Count** | Manual overrides (autonomy measure) | Counter |
| **Context Drift Score** | Embedding distance between task prompt and current state | Gauge |
| **Variance Across Models** | Same loop on GPT-4o vs Claude vs Gemini | Gauge |
| **Output/Input Token Ratio** | Efficiency of token usage | Gauge |

### Cost-Quality Pareto Frontier

Expose tradeoffs explicitly, not hidden:

```
For task type X:
  - Budget: $0.05/run → Quality P50: 0.72, Steps P50: 4
  - Budget: $0.15/run → Quality P50: 0.89, Steps P50: 7
  - Budget: $0.50/run → Quality P50: 0.94, Steps P50: 12
```

### Removed Metrics (with rationale)

| Removed | Reason |
|---|---|
| Cache Hit Rate | Ambiguous without cache contract definition |
| Concurrency Utilization | Meaningless without managed pool |
| Token Efficiency (useful/total) | "Useful tokens" undefined; replaced with output/input ratio |
| Quality Score (LLM-as-judge) | Renamed to "Quality Signal (unreliable)" — position/verbosity bias |
| Checkpoint Frequency | Config parameter, not metric |

### Metric Conflicts (Pareto)

| Optimization Target | Trades Against | Severity |
|---|---|---|
| Cost minimization | Quality Score | High — fundamental tension |
| Steps to Completion | Quality | Medium — measurable, tunable |
| Token Efficiency | Robustness | Medium — shorter prompts = less context |

## Storage Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  In-Memory   │────▶│  SQLite      │────▶│  External       │
│  (real-time) │     │  (post-run)  │     │  (Prometheus/   │
│              │     │              │     │   PostHog)      │
└─────────────┘     └──────────────┘     └─────────────────┘
     │                    │                       │
     └────────────────────┴───────────────────────┘
                    Collector Pattern
```

**Collector pattern:** Loop engine emits events. Collectors aggregate into whatever backend makes sense. The loop engine does NOT know about Prometheus — it emits events that a collector can route.

## Event Schema

```python
@dataclass
class LoopEvent:
    job_id: str
    event_type: str          # "step_started", "step_completed", "checkpoint", "error", "completed"
    timestamp: float
    step_index: int
    metrics_snapshot: dict   # cost_so_far, tokens_used, estimated_remaining
    payload: dict            # step-specific data
```

## Integration

### OpenTelemetry Spans

```
loop.engine.start          → span start
loop.engine.step           → child span per step
loop.engine.checkpoint     → event on the span
loop.engine.complete       → span end with status
loop.engine.error          → span end with error
```

### Agent-Compatible Events

Minimal `LoopEvent` schema for agents that don't have an observability stack. Both OTel and LoopEvent emitted from the same source.
