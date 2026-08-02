"""Prompt templates for the dist-kg server: ``trace_provenance(subject)``."""

from __future__ import annotations

from mcp.server import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.prompt()
    def trace_provenance(subject: str) -> str:
        """Instructions for answering provenance questions against the KG.

        Args:
            subject: The thing whose provenance to trace (run id, artifact
                path, or model id).
        """
        return (
            "You are answering a provenance question about the distribution "
            "suite knowledge graph.\n"
            f"Subject: {subject}\n\n"
            "Follow these steps:\n"
            "1. Resolve the subject to a KG node with query_provenance, passing "
            "exactly one of artifact_path, run_id, or model_id. Resolution "
            "follows the node-id scheme: artifact_path -> artifact:<normpath>, "
            "run_id -> run:<run_id>, model_id -> model:<model_id>.\n"
            "2. Read the returned node and its neighbors to see how the subject "
            "relates to runs, artifacts and models via the relations "
            "has_artifact, generated_by, derived_from and references.\n"
            "3. For deeper ancestry/descendancy call get_provenance_chain("
            'node_id, direction="up"|"down") — "up" walks incoming edges with '
            "relations derived_from/generated_by/references, \"down\" walks "
            "outgoing edges with relations derived_from/has_artifact.\n"
            "4. For a compact neighborhood view, read the kg://graph/{node_id} "
            "resource (the node plus 1-hop neighbors in both directions with "
            "edge metadata).\n"
            "5. Answer the user's question citing the concrete node ids, "
            "relations, and edge metadata you observed. If the subject cannot "
            "be resolved, say so and suggest a search_nodes call to find "
            "matching runs/artifacts/models."
        )
