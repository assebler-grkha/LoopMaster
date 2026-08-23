"""Metrics collection for loop execution."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MetricPoint:
    """A single metric data point."""

    name: str
    value: float
    timestamp: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class LoopMetrics:
    """Aggregated metrics for a loop execution."""

    loop_name: str
    total_cost: float = 0.0
    total_tokens: int = 0
    steps_executed: int = 0
    step_durations_ms: list[float] = field(default_factory=list)
    step_costs: list[float] = field(default_factory=list)
    errors: int = 0
    retries: int = 0
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_ms(self) -> float:
        """Total loop duration in milliseconds."""
        return (self.end_time - self.start_time) * 1000

    @property
    def cost_per_step(self) -> float:
        """Average cost per step."""
        return self.total_cost / max(self.steps_executed, 1)

    @property
    def tokens_per_step(self) -> float:
        """Average tokens per step."""
        return self.total_tokens / max(self.steps_executed, 1)

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to a dictionary."""
        return {
            "loop_name": self.loop_name,
            "total_cost": self.total_cost,
            "total_tokens": self.total_tokens,
            "steps_executed": self.steps_executed,
            "step_durations_ms": list(self.step_durations_ms),
            "step_costs": list(self.step_costs),
            "duration_ms": self.duration_ms,
            "cost_per_step": self.cost_per_step,
            "tokens_per_step": self.tokens_per_step,
            "errors": self.errors,
            "retries": self.retries,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopMetrics:
        """Restore metrics from a serialized dictionary."""
        return cls(
            loop_name=data["loop_name"],
            total_cost=data.get("total_cost", 0.0),
            total_tokens=data.get("total_tokens", 0),
            steps_executed=data.get("steps_executed", 0),
            step_durations_ms=data.get("step_durations_ms", []),
            step_costs=data.get("step_costs", []),
            errors=data.get("errors", 0),
            retries=data.get("retries", 0),
            start_time=data.get("start_time", 0.0),
            end_time=data.get("end_time", 0.0),
        )


class MetricsCollector:
    """Collects and stores loop execution metrics.

    Storage layers:
    - In-memory: real-time during execution
    - JSON file: post-run persistence
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._points: list[MetricPoint] = []
        self._loops: dict[str, LoopMetrics] = {}
        self._storage_dir = Path(storage_dir) if storage_dir else None

    def record(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a metric data point."""
        point = MetricPoint(
            name=name,
            value=value,
            timestamp=time.time(),
            tags=tags or {},
        )
        self._points.append(point)

    def start_loop(self, loop_name: str) -> None:
        """Start tracking a loop execution."""
        self._loops[loop_name] = LoopMetrics(
            loop_name=loop_name,
            start_time=time.time(),
        )

    def end_loop(self, loop_name: str) -> LoopMetrics | None:
        """End tracking a loop execution and return metrics."""
        if loop_name not in self._loops:
            return None
        metrics = self._loops[loop_name]
        metrics.end_time = time.time()
        return metrics

    def record_step(
        self,
        loop_name: str,
        step_name: str,
        cost: float,
        tokens: int,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """Record step execution metrics."""
        if loop_name not in self._loops:
            self.start_loop(loop_name)

        m = self._loops[loop_name]
        m.total_cost += cost
        m.total_tokens += tokens
        m.steps_executed += 1
        m.step_durations_ms.append(duration_ms)
        m.step_costs.append(cost)
        if not success:
            m.errors += 1

        self.record("step.cost", cost, {"loop": loop_name, "step": step_name})
        self.record("step.tokens", tokens, {"loop": loop_name, "step": step_name})
        self.record("step.duration_ms", duration_ms, {"loop": loop_name, "step": step_name})

    def record_retry(self, loop_name: str, step_name: str) -> None:
        """Record a retry event."""
        if loop_name in self._loops:
            self._loops[loop_name].retries += 1
        self.record("step.retry", 1, {"loop": loop_name, "step": step_name})

    def get_loop_metrics(self, loop_name: str) -> LoopMetrics | None:
        """Get metrics for a specific loop."""
        return self._loops.get(loop_name)

    def get_all_metrics(self) -> dict[str, LoopMetrics]:
        """Get all loop metrics."""
        return dict(self._loops)

    def get_all_points(self) -> list[MetricPoint]:
        """Get all recorded metric points."""
        return list(self._points)

    def save(self, filepath: str | Path) -> None:
        """Save metrics to a JSON file."""
        data = {
            "points": [
                {
                    "name": p.name,
                    "value": p.value,
                    "timestamp": p.timestamp,
                    "tags": p.tags,
                }
                for p in self._points
            ],
            "loops": {name: m.to_dict() for name, m in self._loops.items()},
        }
        Path(filepath).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, filepath: str | Path) -> None:
        """Load metrics from a JSON file."""
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        for p in data.get("points", []):
            self._points.append(
                MetricPoint(
                    name=p["name"],
                    value=p["value"],
                    timestamp=p["timestamp"],
                    tags=p.get("tags", {}),
                )
            )
        for name, m_data in data.get("loops", {}).items():
            self._loops[name] = LoopMetrics.from_dict(m_data)

    def to_otlp_payload(self, service_name: str = "loopmaster") -> dict[str, Any]:
        """Convert collected metrics to standard OpenTelemetry OTLP/HTTP JSON metric format."""
        metrics_list: list[dict[str, Any]] = []
        for p in self._points:
            ts_nano = str(int(p.timestamp * 1_000_000_000))
            attrs = [{"key": k, "value": {"stringValue": str(v)}} for k, v in p.tags.items()]
            dp: dict[str, Any] = {
                "timeUnixNano": ts_nano,
                "asDouble": float(p.value),
                "attributes": attrs,
            }
            metrics_list.append(
                {
                    "name": p.name,
                    "gauge": {"dataPoints": [dp]},
                }
            )

        return {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": service_name}}
                        ]
                    },
                    "scopeMetrics": [
                        {
                            "scope": {"name": "loopmaster", "version": "1.0.0"},
                            "metrics": metrics_list,
                        }
                    ],
                }
            ]
        }
