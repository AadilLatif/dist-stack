"""JSON-serialization helpers shared by ecosystem MCP servers.

Small, stdlib-only utilities for the JSON-string return convention documented
in ``CONVENTIONS.md``. Tools use ``error_payload`` for failures and
``json_safe`` to encode success payloads without raising on non-JSON values.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID


def _default(obj: Any) -> Any:
    """Coerce one non-JSON value into something serializable."""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj):
        return {f.name: getattr(obj, f.name) for f in fields(obj)}
    return str(obj)


def json_safe(obj: Any, **kwargs: Any) -> str:
    """JSON-encode ``obj``, coercing non-serializable values instead of raising.

    Datetimes become ISO-8601 strings, sets become lists, enums become their
    ``value``, paths/UUIDs stringify, dataclasses become dicts, and anything
    else falls back to ``str()``. Extra ``kwargs`` are forwarded to
    ``json.dumps``.
    """
    return json.dumps(obj, default=_default, **kwargs)


def error_payload(message: str, **extra: Any) -> str:
    """Standard error payload string: ``{"success": False, "error": ...}``.

    Tools return this instead of raising, per the CONVENTIONS.md contract.
    """
    return json_safe({"success": False, "error": message, **extra})
