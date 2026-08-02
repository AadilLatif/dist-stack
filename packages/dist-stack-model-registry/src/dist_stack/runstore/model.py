"""Data models for a stored runstore record."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["RunRecord", "ArtifactRecord"]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tool: str
    run_type: str
    status: str
    implementation: str | None = None
    message: str | None = None
    session_id: str | None = None
    tool_version: str | None = None
    model_id: str | None = None
    model_version: int | None = None
    model_hash: str | None = None
    payload: dict = field(default_factory=dict)  # parsed JSON, {} when NULL
    created_at_utc: str | None = None  # ISO-8601 UTC
    updated_at_utc: str | None = None
    deleted_at_utc: str | None = None

    @property
    def success(self) -> bool | None:
        """Terminal-success mapping; ``None`` for non-terminal statuses."""
        return {"succeeded": True, "failed": False, "cancelled": False}.get(
            self.status
        )


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    artifact_path: str  # absolute path
    artifact_type: str | None = None
    tool: str | None = None
    tool_version: str | None = None
    model_id: str | None = None
    model_version: int | None = None
    model_hash: str | None = None
    created_at_utc: str | None = None
    deleted_at_utc: str | None = None
