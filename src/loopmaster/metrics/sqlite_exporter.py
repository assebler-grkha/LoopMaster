"""SQLite metrics exporter — persistent queryable storage for loop metrics."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .collector import MetricPoint, MetricsCollector


class SQLiteExporter:
    """Export MetricsCollector data to SQLite for querying and analysis.

    Creates two tables:
      - metric_points: raw metric data points
      - loop_metrics: aggregated per-loop metrics
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._init_tables()
        return self._conn

    def _init_tables(self) -> None:
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS metric_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp REAL NOT NULL,
                tags TEXT
            );
            CREATE TABLE IF NOT EXISTS loop_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loop_name TEXT NOT NULL,
                total_cost REAL NOT NULL,
                total_tokens INTEGER NOT NULL,
                steps_executed INTEGER NOT NULL,
                step_durations_ms TEXT,
                step_costs TEXT,
                errors INTEGER NOT NULL,
                retries INTEGER NOT NULL,
                start_time REAL,
                end_time REAL,
                duration_ms REAL,
                exported_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mp_name ON metric_points(name);
            CREATE INDEX IF NOT EXISTS idx_mp_ts ON metric_points(timestamp);
            CREATE INDEX IF NOT EXISTS idx_lm_name ON loop_metrics(loop_name);
        """)

    def export_collector(self, collector: MetricsCollector) -> int:
        """Export all data from a MetricsCollector. Returns total rows inserted."""
        rows = 0
        rows += self._export_metric_points(collector)
        rows += self._export_loop_metrics(collector)
        self.conn.commit()
        return rows

    def _export_metric_points(self, collector: MetricsCollector) -> int:
        points = [
            (p.name, p.value, p.timestamp, json.dumps(p.tags) if p.tags else None)
            for p in collector._points
        ]
        if not points:
            return 0
        cur = self.conn.cursor()
        cur.executemany(
            "INSERT INTO metric_points (name, value, timestamp, tags) VALUES (?, ?, ?, ?)",
            points,
        )
        return len(points)

    def _export_loop_metrics(self, collector: MetricsCollector) -> int:
        loops_dict = collector.get_all_metrics()
        if not loops_dict:
            return 0
        import time

        now = time.time()
        rows = []
        for m in loops_dict.values():
            rows.append(
                (
                    m.loop_name,
                    m.total_cost,
                    m.total_tokens,
                    m.steps_executed,
                    json.dumps(m.step_durations_ms),
                    json.dumps(m.step_costs),
                    m.errors,
                    m.retries,
                    m.start_time,
                    m.end_time,
                    m.duration_ms,
                    now,
                )
            )
        cur = self.conn.cursor()
        cur.executemany(
            """INSERT INTO loop_metrics
               (loop_name, total_cost, total_tokens, steps_executed,
                step_durations_ms, step_costs, errors, retries,
                start_time, end_time, duration_ms, exported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        return len(rows)

    def export_metric_points(self, points: list[MetricPoint]) -> int:
        """Export raw MetricPoint objects directly."""
        rows = [
            (p.name, p.value, p.timestamp, json.dumps(p.tags) if p.tags else None) for p in points
        ]
        if not rows:
            return 0
        cur = self.conn.cursor()
        cur.executemany(
            "INSERT INTO metric_points (name, value, timestamp, tags) VALUES (?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def query_points(
        self,
        name: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query metric points with optional filters."""
        sql = "SELECT * FROM metric_points WHERE 1=1"
        params: list[Any] = []
        if name:
            sql += " AND name = ?"
            params.append(name)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def query_loops(
        self,
        loop_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query loop metrics with optional name filter."""
        sql = "SELECT * FROM loop_metrics WHERE 1=1"
        params: list[Any] = []
        if loop_name:
            sql += " AND loop_name = ?"
            params.append(loop_name)
        sql += " ORDER BY exported_at DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def summary(self) -> dict[str, Any]:
        """Get a summary of all exported data."""
        cur = self.conn.execute("SELECT COUNT(*) FROM metric_points")
        point_count = cur.fetchone()[0]
        cur = self.conn.execute("SELECT COUNT(*) FROM loop_metrics")
        loop_count = cur.fetchone()[0]
        cur = self.conn.execute(
            "SELECT name, COUNT(*) as cnt FROM metric_points GROUP BY name ORDER BY cnt DESC"
        )
        points_by_name = {row["name"]: row["cnt"] for row in cur.fetchall()}
        cur = self.conn.execute(
            "SELECT loop_name, total_cost, total_tokens, steps_executed "
            "FROM loop_metrics ORDER BY exported_at DESC LIMIT 10"
        )
        recent_loops = [dict(row) for row in cur.fetchall()]
        return {
            "total_metric_points": point_count,
            "total_loop_metrics": loop_count,
            "points_by_name": points_by_name,
            "recent_loops": recent_loops,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SQLiteExporter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
