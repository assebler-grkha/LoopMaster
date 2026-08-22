# Audit: SQLite exporter accesses private attribute

**Severity:** P3 — Low  
**File:** `metrics/sqlite_exporter.py`  
**Tags:** coupling, maintainability

## Problem

`_export_metric_points` accesses `collector._points` directly (private attribute). Fragile coupling — breaks if internal implementation changes.

## Impact

- Breaks on MetricsCollector refactoring
- Violates encapsulation principle

## Fix Plan

1. Add public property to MetricsCollector:
```python
@property
def points(self) -> list[MetricPoint]:
    return list(self._points)
```

2. Update exporter to use public API:
```python
points = collector.points  # Instead of collector._points
```

### Tests
- Exporter works with public API
- Internal changes don't break exporter
