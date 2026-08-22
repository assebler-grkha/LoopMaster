import asyncio
from dataclasses import dataclass

from loopmaster.utils import LLMProvider, dedent, hash_data, serialize


class TestLLMProvider:
    def test_complete_sync(self):
        class SyncProvider(LLMProvider):
            def complete(self, prompt, model, **kw):
                return f"response to: {prompt} using {model}"

            def count_tokens(self, text, model):
                return len(text.split())

            def models(self):
                return ["model-a"]

        p = SyncProvider()
        assert p.complete("hello", "model-a") == "response to: hello using model-a"
        assert p.count_tokens("a b c", "model-a") == 3
        assert p.models() == ["model-a"]

    def test_complete_async(self):
        class AsyncProvider(LLMProvider):
            async def complete(self, prompt, model, **kw):
                return f"async: {prompt}"

            def count_tokens(self, text, model):
                return 1

            def models(self):
                return ["m1"]

        p = AsyncProvider()
        result = asyncio.run(p.complete("test", "m1"))
        assert result == "async: test"

    def test_cannot_instantiate_abc(self):
        import pytest

        with pytest.raises(TypeError):
            LLMProvider()


class TestSerialize:
    def test_dict(self):
        assert serialize({"a": 1, "b": "two"}) == {"a": 1, "b": "two"}

    def test_list(self):
        assert serialize([1, 2, 3]) == [1, 2, 3]

    def test_nested_dict(self):
        data = {"a": {"b": [1, 2]}}
        assert serialize(data) == {"a": {"b": [1, 2]}}

    def test_dataclass(self):
        @dataclass
        class Point:
            x: int
            y: int

        result = serialize(Point(1, 2))
        assert result == {"x": 1, "y": 2}

    def test_nested_dataclass(self):
        @dataclass
        class Inner:
            val: int

        @dataclass
        class Outer:
            inner: Inner
            name: str

        result = serialize(Outer(inner=Inner(42), name="test"))
        assert result == {"inner": {"val": 42}, "name": "test"}

    def test_mixed_list(self):
        @dataclass
        class Item:
            id: int

        data = [Item(1), {"key": "val"}, [1, 2]]
        result = serialize(data)
        assert result[0] == {"id": 1}
        assert result[1] == {"key": "val"}
        assert result[2] == [1, 2]

    def test_primitives(self):
        assert serialize(42) == 42
        assert serialize("hello") == "hello"
        assert serialize(3.14) == 3.14
        assert serialize(True) is True
        assert serialize(None) is None

    def test_object_with_dict(self):
        class Custom:
            def __init__(self):
                self.x = 1
                self.y = 2

        result = serialize(Custom())
        assert result == {"x": 1, "y": 2}


class TestHashData:
    def test_basic_hash(self):
        h = hash_data("hello world")
        assert isinstance(h, str)
        assert len(h) == 16  # truncated SHA-256

    def test_deterministic(self):
        assert hash_data("test") == hash_data("test")

    def test_different_inputs_different_hashes(self):
        assert hash_data("abc") != hash_data("def")

    def test_empty_string(self):
        h = hash_data("")
        assert len(h) == 16

    def test_dict_input(self):
        h = hash_data({"a": 1})
        assert isinstance(h, str)


class TestDedent:
    def test_basic(self):
        text = "    line1\n    line2"
        result = dedent(text)
        assert result == "line1\nline2"

    def test_already_dedented(self):
        text = "line1\nline2"
        assert dedent(text) == text

    def test_multiline_with_common_indent(self):
        text = "    if True:\n        print('hello')"
        result = dedent(text)
        assert result.startswith("if True:")
