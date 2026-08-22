"""Tests for metrics/collector.py — MetricsCollector."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loopmaster.metrics.collector import LoopMetrics, MetricPoint, MetricsCollector


class TestMetricPoint:
    def test_creation(self):
        mp = MetricPoint(name="step.cost", value=0.05, timestamp=1.0, tags={"loop": "test"})
        assert mp.name == "step.cost"
        assert mp.value == 0.05
        assert mp.tags["loop"] == "test"


class TestLoopMetrics:
    def test_duration_ms(self):
        m = LoopMetrics(loop_name="test", start_time=100.0, end_time=100.5)
        assert m.duration_ms == 500.0

    def test_cost_per_step(self):
        m = LoopMetrics(loop_name="test", total_cost=1.0, steps_executed=5)
        assert m.cost_per_step == 0.2

    def test_tokens_per_step(self):
        m = LoopMetrics(loop_name="test", total_tokens=1000, steps_executed=4)
        assert m.tokens_per_step == 250.0

    def test_zero_steps(self):
        m = LoopMetrics(loop_name="test")
        assert m.cost_per_step == 0.0
        assert m.tokens_per_step == 0.0

    def test_to_dict(self):
        m = LoopMetrics(loop_name="test", total_cost=1.0, total_tokens=500, steps_executed=2)
        d = m.to_dict()
        assert d["loop_name"] == "test"
        assert d["total_cost"] == 1.0
        assert d["steps_executed"] == 2


class TestMetricsCollector:
    def test_record(self):
        mc = MetricsCollector()
        mc.record("step.cost", 0.05, tags={"loop": "test"})
        assert len(mc._points) == 1

    def test_start_end_loop(self):
        mc = MetricsCollector()
        mc.start_loop("myloop")
        m = mc.end_loop("myloop")
        assert m is not None
        assert m.loop_name == "myloop"
        assert m.end_time > 0

    def test_end_loop_not_started(self):
        mc = MetricsCollector()
        assert mc.end_loop("nonexistent") is None

    def test_record_step(self):
        mc = MetricsCollector()
        mc.record_step("loop1", "s1", cost=0.01, tokens=100, duration_ms=50.0)
        m = mc.get_loop_metrics("loop1")
        assert m is not None
        assert m.steps_executed == 1
        assert m.total_cost == 0.01
        assert m.total_tokens == 100
        assert m.step_durations_ms == [50.0]

    def test_record_step_auto_start(self):
        mc = MetricsCollector()
        mc.record_step("auto_loop", "s1", cost=0.01, tokens=100, duration_ms=50.0)
        m = mc.get_loop_metrics("auto_loop")
        assert m is not None

    def test_record_step_failure(self):
        mc = MetricsCollector()
        mc.record_step("loop1", "s1", cost=0.01, tokens=100, duration_ms=50.0, success=False)
        m = mc.get_loop_metrics("loop1")
        assert m.errors == 1

    def test_record_retry(self):
        mc = MetricsCollector()
        mc.start_loop("loop1")
        mc.record_retry("loop1", "s1")
        m = mc.get_loop_metrics("loop1")
        assert m.retries == 1

    def test_get_all_metrics(self):
        mc = MetricsCollector()
        mc.start_loop("l1")
        mc.start_loop("l2")
        all_m = mc.get_all_metrics()
        assert len(all_m) == 2
        assert "l1" in all_m
        assert "l2" in all_m

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MetricsCollector()
            mc.record("custom.metric", 42.0)
            mc.start_loop("loop1")
            mc.record_step("loop1", "s1", cost=0.05, tokens=500, duration_ms=100.0)

            filepath = Path(tmpdir) / "metrics.json"
            mc.save(filepath)
            assert filepath.exists()

            mc2 = MetricsCollector()
            mc2.load(filepath)
            assert len(mc2._points) == 4
