"""LoopSpec v1 — JSON configuration format loader and validator."""

from __future__ import annotations

from .loader import (
    LoopSpec,
    SpecValidationError,
    load_loop_from_dict,
    load_loop_from_json_file,
    parse_loop_spec,
    validate_loop_spec,
)

__all__ = [
    "LoopSpec",
    "SpecValidationError",
    "load_loop_from_dict",
    "load_loop_from_json_file",
    "parse_loop_spec",
    "validate_loop_spec",
]
