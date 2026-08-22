"""Tests for cost/tracker.py — CostTracker."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from loopmaster.cost.tracker import CostTracker


class TestCostTracker:
    def test_calculate_cost_gpt4o(self):
        ct = CostTracker()
        cost = ct.calculate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        expected = 1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_calculate_cost_unknown_model(self):
        ct = CostTracker()
        cost = ct.calculate_cost("unknown-model", input_tokens=1000, output_tokens=1000)
        assert cost > 0  # Uses default rate

    def test_record(self):
        ct = CostTracker()
        cost = ct.record("gpt-4o", input_tokens=1000, output_tokens=500, step_name="search")
        assert cost > 0
        assert ct.total_cost == cost

    def test_total_tokens(self):
        ct = CostTracker()
        ct.record("gpt-4o", input_tokens=1000, output_tokens=500)
        ct.record("gpt-4o-mini", input_tokens=2000, output_tokens=1000)
        assert ct.total_input_tokens == 3000
        assert ct.total_output_tokens == 1500

    def test_budget_tracking(self):
        ct = CostTracker()
        ct.set_budget(0.001)
        assert ct.is_over_budget is False
        assert ct.remaining_budget is not None
        assert ct.remaining_budget > 0

        ct.record("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
        assert ct.is_over_budget is True
        assert ct.remaining_budget == 0.0

    def test_no_budget(self):
        ct = CostTracker()
        assert ct.is_over_budget is False
        assert ct.remaining_budget is None

    def test_cost_by_model(self):
        ct = CostTracker()
        ct.record("gpt-4o", 1000, 500, step_name="s1")
        ct.record("gpt-4o-mini", 1000, 500, step_name="s2")
        by_model = ct.cost_by_model()
        assert "gpt-4o" in by_model
        assert "gpt-4o-mini" in by_model

    def test_cost_by_step(self):
        ct = CostTracker()
        ct.record("gpt-4o", 1000, 500, step_name="search")
        ct.record("gpt-4o", 1000, 500, step_name="analyze")
        by_step = ct.cost_by_step()
        assert "search" in by_step
        assert "analyze" in by_step

    def test_to_dict(self):
        ct = CostTracker()
        ct.record("gpt-4o", 1000, 500, step_name="s1")
        d = ct.to_dict()
        assert "total_cost" in d
        assert "cost_by_model" in d
        assert "cost_by_step" in d

    def test_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ct = CostTracker()
            ct.record("gpt-4o", 1000, 500, step_name="s1")
            filepath = Path(tmpdir) / "costs.json"
            ct.save(filepath)
            assert filepath.exists()
            data = json.loads(filepath.read_text())
            assert len(data["records"]) == 1

    def test_custom_pricing(self):
        ct = CostTracker(pricing={"my-model": {"input": 0.01, "output": 0.02}})
        cost = ct.calculate_cost("my-model", input_tokens=100, output_tokens=100)
        assert cost == 100 * 0.01 + 100 * 0.02
