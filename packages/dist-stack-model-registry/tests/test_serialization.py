"""Tests for dist_stack.mcp.serialization JSON helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import UUID

from dist_stack.mcp import error_payload, json_safe


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class _Point:
    x: float
    y: float


def test_json_safe_plain_payload():
    assert json.loads(json_safe({"success": True, "n": 1})) == {
        "success": True,
        "n": 1,
    }


def test_json_safe_coerces_non_json_values():
    payload = json_safe(
        {
            "when": datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
            "stamp": datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc).date(),
            "color": _Color.RED,
            "tags": {"a", "b"},
            "path": Path("/tmp/x.json"),
            "uid": UUID("12345678-1234-5678-1234-567812345678"),
            "point": _Point(1.5, -2.5),
            "odd": complex(1, 2),
        }
    )
    decoded = json.loads(payload)
    assert decoded["when"] == "2026-07-31T12:00:00+00:00"
    assert decoded["stamp"] == "2026-07-31"
    assert decoded["color"] == "red"
    assert set(decoded["tags"]) == {"a", "b"}
    assert decoded["path"] == "/tmp/x.json"
    assert decoded["uid"] == "12345678-1234-5678-1234-567812345678"
    assert decoded["point"] == {"x": 1.5, "y": -2.5}
    assert decoded["odd"] == "(1+2j)"


def test_json_safe_forwards_kwargs():
    assert json_safe({"b": 1, "a": 2}, sort_keys=True) == '{"a": 2, "b": 1}'


def test_error_payload_shape():
    assert json.loads(error_payload("boom")) == {"success": False, "error": "boom"}


def test_error_payload_extra_fields():
    payload = json.loads(error_payload("boom", code=42, detail={"k": "v"}))
    assert payload == {
        "success": False,
        "error": "boom",
        "code": 42,
        "detail": {"k": "v"},
    }
