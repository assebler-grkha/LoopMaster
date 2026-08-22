"""Tests for core/context.py — Context with immutable snapshots."""

from __future__ import annotations

from loopmaster.core.context import Context


class TestContext:
    def test_getattr_setattr(self):
        ctx = Context()
        ctx.x = 42
        assert ctx.x == 42

    def test_getattr_missing(self):
        ctx = Context()
        try:
            _ = ctx.missing
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_contains(self):
        ctx = Context()
        ctx.a = 1
        assert "a" in ctx
        assert "b" not in ctx

    def test_get_method(self):
        ctx = Context()
        ctx.x = 10
        assert ctx.get("x") == 10
        assert ctx.get("y", "default") == "default"

    def test_snapshot_returns_copy(self):
        ctx = Context()
        ctx.x = 1
        snap = ctx.snapshot()
        ctx.x = 2
        assert snap["x"] == 1

    def test_merge_updates(self):
        ctx = Context()
        ctx.x = 1
        ctx.merge({"x": 99, "y": 2})
        assert ctx.x == 99
        assert ctx.y == 2

    def test_to_dict(self):
        ctx = Context()
        ctx.a = 1
        ctx.b = "hello"
        d = ctx.to_dict()
        assert d == {"a": 1, "b": "hello"}
        # to_dict returns deep copy
        d["a"] = 999
        assert ctx.a == 1

    def test_from_dict(self):
        ctx = Context.from_dict({"x": 1, "y": 2})
        assert ctx.x == 1
        assert ctx.y == 2

    def test_roundtrip(self):
        ctx = Context()
        ctx.name = "test"
        ctx.items = [1, 2, 3]
        ctx2 = Context.from_dict(ctx.to_dict())
        assert ctx2.name == "test"
        assert ctx2.items == [1, 2, 3]
        # Deep copy — mutation doesn't affect original
        ctx2.items.append(4)
        assert len(ctx.to_dict()["items"]) == 3

    def test_summary_empty(self):
        ctx = Context()
        assert ctx.summary() == "Context(empty)"

    def test_summary_with_keys(self):
        ctx = Context()
        ctx.a = 1
        ctx.b = 2
        s = ctx.summary()
        assert "a" in s
        assert "b" in s

    def test_private_attrs(self):
        ctx = Context()
        ctx._internal = "hidden"
        assert ctx._internal == "hidden"
        # Not in snapshot
        assert "_internal" not in ctx.snapshot()
