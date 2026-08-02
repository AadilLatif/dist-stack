"""Data models for the knowledge graph store."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["KGNode", "KGEdge", "KGStats", "IngestReport"]


@dataclass(frozen=True)
class KGNode:
    node_id: str
    node_type: str
    label: str | None = None
    artifact_path: str | None = None
    run_id: str | None = None
    model_id: str | None = None
    tool: str | None = None
    tool_version: str | None = None
    metadata: dict = field(default_factory=dict)  # parsed JSON, {} when NULL
    created_at_utc: str | None = None  # ISO-8601 UTC
    updated_at_utc: str | None = None
    deleted_at_utc: str | None = None


@dataclass(frozen=True)
class KGEdge:
    edge_id: str
    source_node: str
    target_node: str
    relation: str
    metadata: dict = field(default_factory=dict)  # parsed JSON, {} when NULL
    created_at_utc: str | None = None  # ISO-8601 UTC
    deleted_at_utc: str | None = None


@dataclass(frozen=True)
class KGStats:
    node_counts: dict[str, int] = field(default_factory=dict)  # by node_type
    edge_counts: dict[str, int] = field(default_factory=dict)  # by relation
    top_degree: list[tuple[str, int]] = field(default_factory=list)  # (node_id, degree)
    updated_at_utc: str | None = None


@dataclass(frozen=True)
class IngestReport:
    kg_db: str
    pass_started_at_utc: str
    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_updated: int
    derived_from_unresolved: list[str] = field(default_factory=list)
    derived_from_uri_skipped: int = 0
    sidecar_missing: int = 0
    errors: list[str] = field(default_factory=list)
