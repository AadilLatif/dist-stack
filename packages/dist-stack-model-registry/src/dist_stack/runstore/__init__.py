"""Public surface of the runstore (`dist_stack.runstore`).

Re-exports the functional API, the ``RunRecord``/``ArtifactRecord`` dataclasses,
and the exception classes. Nothing else is public. MCP exposure is explicitly
deferred to Phase 2 (`dist_stack.mcp`).
"""

from __future__ import annotations

from .api import (
    attach_artifact,
    create_run,
    delete_run,
    ensure_schema,
    get_run,
    get_runstore_path,
    list_artifacts,
    list_runs,
    make_run_id,
    update_run,
)
from .errors import (
    ArtifactPathNotFoundError,
    RunExistsError,
    RunNotFoundError,
    RunstoreError,
    RunstoreUnavailableError,
)
from .model import ArtifactRecord, RunRecord

__all__ = [
    "create_run",
    "get_run",
    "list_runs",
    "update_run",
    "delete_run",
    "attach_artifact",
    "list_artifacts",
    "make_run_id",
    "ensure_schema",
    "get_runstore_path",
    "RunRecord",
    "ArtifactRecord",
    "RunstoreError",
    "RunstoreUnavailableError",
    "RunNotFoundError",
    "RunExistsError",
    "ArtifactPathNotFoundError",
]
