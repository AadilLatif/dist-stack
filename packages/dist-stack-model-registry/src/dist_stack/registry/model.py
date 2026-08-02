"""Data model for a stored model registry record."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ModelRecord"]


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    version: int
    stored_path: str  # verbatim stored value when resolve_path=False; absolute when resolve_path=True
    model_hash: str | None = None
    metadata: dict = field(default_factory=dict)  # parsed JSON, {} when NULL
    created_at_utc: str | None = None  # ISO-8601 UTC
    deleted_at_utc: str | None = None
