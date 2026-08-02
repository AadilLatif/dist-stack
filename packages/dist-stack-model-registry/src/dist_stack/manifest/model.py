"""Data model and constants for the provenance manifest sidecar.

The v1 manifest sidecar is an immutable JSON file written next to every
artifact, recording what produced it, from what, and when. See oracle doc 09.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_SUFFIX",
    "Manifest",
]

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class Manifest:
    """v1 provenance sidecar record.

    Required fields are declared before optional ones (dataclass constraint:
    a defaulted field cannot precede a non-defaulted field).
    """

    artifact_path: str  # path to the artifact this describes
    artifact_type: str  # e.g. "gdm_system", "gdm_flow_run", "erad_simulation", "ditto_conversion", "shift_feeder"
    tool: str  # tool name that created the artifact
    tool_version: str  # tool version
    schema_version: int = MANIFEST_SCHEMA_VERSION
    model_id: str | None = None  # from registry
    model_version: int | None = None  # from registry
    model_hash: str | None = None  # opaque hash string
    package: str | None = None  # package name
    package_version: str | None = None  # package version
    config: dict = field(default_factory=dict)  # snapshot of relevant config
    derived_from: list[str] = field(default_factory=list)  # parent artifact paths
    created_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
