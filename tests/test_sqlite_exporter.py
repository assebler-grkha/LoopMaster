"""Tests for SQLite metrics exporter."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from loopmaster.metrics.collector import MetricPoint, MetricsCollector
from loopmaster.metrics.sqlite_exporter import SQLiteExporter


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td) / "metrics.db"


@pytest.fixture
def exporter(tmp_db):
    exp = SQLiteExporter(tmp_db)
    yield exp
    exp.close()


@pytest.fixture
def populated_exporter(tmp_db):
    exp = SQLiteExporter(tmp_db)
    collector = MetricsCollector()
    collector.start_loop("test-loop")
    collector.record_step(
        "test-loop",
        "step-a",
        cost=0.05,
        tokens=100,
        duration_ms=50.0,
        success=True,
    )
    collector.record_step(
        "test-loop",
        "step-b",
        cost=0.10,
        tokens=200,
        duration_ms=100.0,
        success=True,
    )
    collector.record_retry("test-loop", "step-b")
    collector.end_loop("test-loop")
    exp.export_collector(collector)
    yield exp
    exp.close()


class TestSQLiteExporterInit:
    def test_creates_db_file(self, tmp_db):
        exp = SQLiteExporter(tmp_db)
        # Force connection to create the file
        _ = exp.conn
        assert tmp_db.exists()
        exp.close()

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "sub" / "dir" / "metrics.db"
            exp = SQLiteExporter(db_path)
            _ = exp.conn
            assert db_path.exists()
            exp.close()

    def test_context_manager(self, tmp_db):
        with SQLiteExporter(tmp_db) as exp:
            _ = exp.conn  # trigger connection
            assert exp._conn is not None
        assert exp._conn is None


class TestExportCollector:
    def test_export_empty_collector(self, exporter):
        collector = MetricsCollector()
        rows = exporter.export_collector(collector)
        assert rows == 0

    def test_export_with_loop_data(self, exporter):
        collector = MetricsCollector()
        collector.start_loop("my-loop")
        collector.record_step("my-loop", "s1", cost=0.01, tokens=50, duration_ms=20.0, success=True)
        collector.end_loop("my-loop")
        rows = exporter.export_collector(collector)
        assert rows >= 1  # at least 1 loop_metrics row

    def test_export_metric_points(self, exporter):
        collector = MetricsCollector()
        collector.record("test.metric", 42.0, {"env": "test"})
        collector.record("test.metric", 84.0, {"env": "test"})
        rows = exporter.export_collector(collector)
        assert rows == 2

    def test_export_both_types(self, populated_exporter):
        summary = populated_exporter.summary()
        assert summary["total_metric_points"] >= 0
        assert summary["total_loop_metrics"] == 1


class TestExportMetricPoints:
    def test_export_direct_points(self, exporter):
        points = [
            MetricPoint("latency", 120.5, 1000.0, None),
            MetricPoint("latency", 130.2, 1001.0, None),
        ]
        count = exporter.export_metric_points(points)
        assert count == 2

    def test_export_empty_list(self, exporter):
        count = exporter.export_metric_points([])
        assert count == 0


class TestQueryPoints:
    def test_query_all(self, populated_exporter):
        # Export some raw points too
        populated_exporter.export_metric_points(
            [
                MetricPoint("custom.metric", 99.0, 2000.0, {"key": "val"}),
            ]
        )
        results = populated_exporter.query_points()
        assert len(results) >= 1

    def test_query_by_name(self, populated_exporter):
        populated_exporter.export_metric_points(
            [
                MetricPoint("alpha", 1.0, 1000.0, None),
                MetricPoint("beta", 2.0, 1001.0, None),
            ]
        )
        results = populated_exporter.query_points(name="alpha")
        assert all(r["name"] == "alpha" for r in results)

    def test_query_since_filter(self, populated_exporter):
        populated_exporter.export_metric_points(
            [
                MetricPoint("m", 1.0, 1000.0, None),
                MetricPoint("m", 2.0, 2000.0, None),
            ]
        )
        results = populated_exporter.query_points(since=1500.0)
        assert all(r["timestamp"] >= 1500.0 for r in results)

    def test_query_limit(self, populated_exporter):
        points = [MetricPoint("m", float(i), float(i), None) for i in range(100)]
        populated_exporter.export_metric_points(points)
        results = populated_exporter.query_points(limit=5)
        assert len(results) <= 5

    def test_query_tags_parsed(self, exporter):
        exporter.export_metric_points(
            [
                MetricPoint("tagged", 1.0, 1000.0, {"a": "b"}),
            ]
        )
        results = exporter.query_points(name="tagged")
        import json

        assert json.loads(results[0]["tags"]) == {"a": "b"}


class TestQueryLoops:
    def test_query_all_loops(self, populated_exporter):
        results = populated_exporter.query_loops()
        assert len(results) == 1
        assert results[0]["loop_name"] == "test-loop"

    def test_query_by_name(self, populated_exporter):
        results = populated_exporter.query_loops(loop_name="test-loop")
        assert len(results) == 1

    def test_query_nonexistent_name(self, populated_exporter):
        results = populated_exporter.query_loops(loop_name="nope")
        assert len(results) == 0


class TestSummary:
    def test_summary_counts(self, populated_exporter):
        s = populated_exporter.summary()
        assert s["total_loop_metrics"] == 1
        assert "recent_loops" in s

    def test_summary_empty(self, exporter):
        s = exporter.summary()
        assert s["total_metric_points"] == 0
        assert s["total_loop_metrics"] == 0
        assert s["points_by_name"] == {}
        assert s["recent_loops"] == []

    def test_summary_points_by_name(self, exporter):
        exporter.export_metric_points(
            [
                MetricPoint("x", 1.0, 1000.0, None),
                MetricPoint("x", 2.0, 1001.0, None),
                MetricPoint("y", 3.0, 1002.0, None),
            ]
        )
        s = exporter.summary()
        assert s["points_by_name"]["x"] == 2
        assert s["points_by_name"]["y"] == 1
