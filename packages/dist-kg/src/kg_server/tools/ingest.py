"""Ingestion tool: ``ingest``.

``dist_stack.kg.ingest`` is landing in a parallel lane, so it is imported
lazily inside the tool body: the server works standalone with the query tools
even when the ingest module is not yet installed, returning a clean
``{"success": False, "error": "ingest module not available yet"}`` payload.
"""

from __future__ import annotations

from dataclasses import asdict

from dist_stack.kg import KGUnavailableError, NodeNotFoundError
from dist_stack.mcp.serialization import error_payload, json_safe

from mcp.server import MCPServer

INGEST_UNAVAILABLE_ERROR = "ingest module not available yet"


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def ingest(
        runstore_db: str | None = None,
        registry_db: str | None = None,
        manifest_dir: str | None = None,
        prune: bool = False,
    ) -> str:
        """(Re)build the knowledge graph from runstore, registry and sidecars.

        Requires ``dist_stack.kg.ingest`` (a sibling lane) and the KG DB path
        from ``DIST_STACK_KG_DB``. Idempotent; ``prune=True`` soft-deletes
        nodes/edges not refreshed by the pass (mirror mode).

        Args:
            runstore_db: Runstore DB path (default: DIST_STACK_RUNSTORE_DB).
            registry_db: Model registry DB path (default:
                DIST_STACK_MODEL_REGISTRY_DB).
            manifest_dir: Optional sweep for unattached sidecar manifests.
            prune: Soft-delete stale nodes/edges (default False).

        Returns:
            JSON ``{"success", "report"}`` with the IngestReport record, or
            ``{"success": False, "error": ...}``.
        """
        try:
            from dist_stack.kg.ingest import ingest as run_ingest
        except ImportError:
            return error_payload(INGEST_UNAVAILABLE_ERROR)
        try:
            report = run_ingest(
                runstore_db=runstore_db,
                registry_db=registry_db,
                manifest_dir=manifest_dir,
                prune=prune,
            )
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        return json_safe({"success": True, "report": asdict(report)})
