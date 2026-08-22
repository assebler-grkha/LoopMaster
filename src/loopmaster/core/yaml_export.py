"""YAML export for loop definitions.

Serializes a LoopDef (and its steps) to a YAML string.
This is export-only — YAML cannot round-trip back to Python loops.
"""

from __future__ import annotations

import io
from typing import Any

import yaml

from .types import LoopDef


def _represent_dict(dumper: yaml.Dumper, data: dict[str, Any]) -> yaml.Node:
    """Represent dicts with block mapping style."""
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


def _represent_list(dumper: yaml.Dumper, data: list[Any]) -> yaml.Node:
    """Represent lists with block sequence style."""
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data)


def _make_dumper(stream: io.StringIO) -> yaml.Dumper:
    """Create a configured YAML dumper."""
    dumper = yaml.Dumper(stream, default_flow_style=False, sort_keys=False)
    dumper.add_representer(dict, _represent_dict)
    dumper.add_representer(list, _represent_list)
    return dumper


def export_loop(loop_def: LoopDef) -> str:
    """Serialize a LoopDef to a YAML string.

    Args:
        loop_def: The loop definition to serialize.

    Returns:
        YAML string representation of the loop.
    """
    stream = io.StringIO()
    dumper = _make_dumper(stream)

    data = loop_def.to_dict()
    dumper.open()
    dumper.represent(data)
    dumper.close()

    return stream.getvalue()
