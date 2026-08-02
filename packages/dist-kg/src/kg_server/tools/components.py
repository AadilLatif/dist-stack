"""Component-node ingestion via the gdm MCP client (Phase 3f, doc 12 §B.2).

``ingest_components`` resolves a distribution system (``system_path`` XOR
``model_id``), spawns the gdm MCP server, calls ``query_components`` for every
component and — for ``depth >= 2`` — ``get_component_relationships`` per
component, then upserts ``component`` nodes plus ``has_component``
(system → component) and ``parent_of`` (component → component) edges into the
KG.

Key space (frozen in doc 12 §B.2):

- component node: ``component:<system_model_id>:<uuid>``, node_type
  ``component``, label = component name, metadata
  ``{component_type, feeder, substation, phases, in_service}``;
- system node: ``artifact:<normpath(abs_path)>``, node_type ``gdm_system``
  (the same scheme the sidecar ingester uses for ``gdm_system``), anchoring the
  ``has_component`` edges;
- edges: ``has_component`` (system → component), ``parent_of``
  (parent component → child component, from the relationship pass).

``system_model_id`` is the resolved registry model_id when given, else a slug
of the system_path basename (best-effort reverse registry lookup first).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dist_stack.kg import (
    KGUnavailableError,
    NodeNotFoundError,
    get_kg_path,
    upsert_edge,
    upsert_node,
)
from dist_stack.mcp.serialization import error_payload, json_safe

from mcp.server import MCPServer

from kg_server.gdm_client import GdmClient, GdmClientError

COMPONENT_NODE_TYPE = "component"
SYSTEM_NODE_TYPE = "gdm_system"
SYSTEM_MODEL_ID_KEY = "system_model_id"

XOR_ERROR = "ingest_components requires exactly one of system_path, model_id"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "system"


def _system_model_id_from_path(system_path: str) -> str:
    return _slugify(Path(system_path).stem)


def _system_node_id(system_path: str) -> str:
    return "artifact:" + os.path.normpath(os.path.abspath(os.path.expanduser(str(system_path))))


def _component_node_id(system_model_id: str, uuid: str) -> str:
    return f"component:{system_model_id}:{uuid}"


def _resolve_model_id_for_path(system_path: str) -> str | None:
    """Best-effort reverse lookup: registry record whose ``stored_path`` matches.

    Returns ``None`` when the registry is unavailable, empty, or has no match
    (the caller falls back to a basename slug).
    """
    try:
        from dist_stack.registry import list_models
    except ImportError:
        return None
    try:
        target = os.path.normpath(os.path.abspath(system_path))
        for record in list_models():
            stored = record.stored_path
            if stored and os.path.normpath(os.path.abspath(stored)) == target:
                return record.model_id
    except Exception:
        return None
    return None


def _resolve_system(system_path: str | None, model_id: str | None) -> tuple[str, str]:
    """Return ``(system_path_for_gdm, system_model_id)`` from the XOR inputs.

    ``model_id`` is authoritative: resolved via ``dist_stack.registry.lookup``
    to its stored path (passed to gdm as ``system_path``). With only a
    ``system_path``, the model_id is a best-effort reverse lookup, else a slug
    of the basename.
    """
    if model_id is not None:
        from dist_stack.registry import RegistryError, lookup

        try:
            record = lookup(model_id)
        except RegistryError as exc:
            raise ValueError(
                f"cannot resolve model_id={model_id!r} via the model registry: {exc}"
            ) from exc
        return record.stored_path, record.model_id
    return str(system_path), (
        _resolve_model_id_for_path(str(system_path)) or _system_model_id_from_path(str(system_path))
    )


def _component_metadata(comp: dict[str, Any]) -> dict:
    return {
        "component_type": comp.get("component_type"),
        "feeder": comp.get("feeder"),
        "substation": comp.get("substation"),
        "phases": comp.get("phases"),
        "in_service": comp.get("in_service"),
    }


def _parent_of_edges(
    by_uuid: dict[str, str], uuid: str, relationships: dict[str, Any]
) -> list[tuple[str, str]]:
    """``(parent_node_id, child_node_id)`` pairs from one relationships payload.

    Only pairs whose endpoints are in ``by_uuid`` (i.e. components seen in the
    query pass) are returned — an unknown endpoint would violate the edge FK.
    """
    edges: set[tuple[str, str]] = set()
    child_id = by_uuid[uuid]
    for parent in relationships.get("parents") or []:
        parent_uuid = str(parent.get("uuid") or "")
        if parent_uuid in by_uuid:
            edges.add((by_uuid[parent_uuid], child_id))
    for child in relationships.get("children") or []:
        child_uuid = str(child.get("uuid") or "")
        if child_uuid in by_uuid:
            edges.add((child_id, by_uuid[child_uuid]))
    return sorted(edges)


async def _ingest_components(
    *,
    system_path: str | None,
    model_id: str | None,
    depth: int,
) -> dict[str, Any]:
    errors: list[str] = []
    system_path, system_model_id = _resolve_system(system_path, model_id)
    client = GdmClient()

    # -- query pass: every component -----------------------------------------
    query = await client.call("query_components", {"system_path": system_path})
    if query.get("success") is False:
        raise ValueError(query.get("error", "gdm query_components failed"))
    raw_components = query.get("components") or []
    if not isinstance(raw_components, list):
        raise ValueError(f"gdm query_components returned unexpected payload: {query!r}")

    # -- system node anchor ----------------------------------------------------
    system_node_id = _system_node_id(system_path)
    try:
        upsert_node(
            system_node_id, SYSTEM_NODE_TYPE,
            label=Path(system_path).name,
            artifact_path=system_path,
            model_id=system_model_id,
            metadata={SYSTEM_MODEL_ID_KEY: system_model_id},
        )
    except ValueError as exc:  # KGUnavailable / invalid node_type
        raise ValueError(f"cannot upsert system node {system_node_id}: {exc}") from exc

    # -- component nodes + has_component edges --------------------------------
    components_ingested = 0
    edges_added = 0
    touched_edges: set[tuple[str, str, str]] = set()
    by_uuid: dict[str, str] = {}
    for comp in raw_components:
        uuid = str(comp.get("uuid") or comp.get("component_uuid") or "").strip()
        if not uuid:
            errors.append("component without uuid skipped")
            continue
        node_id = _component_node_id(system_model_id, uuid)
        try:
            upsert_node(
                node_id, COMPONENT_NODE_TYPE,
                label=comp.get("name") or uuid,
                model_id=system_model_id,
                metadata=_component_metadata(comp),
            )
            by_uuid[uuid] = node_id
            components_ingested += 1
        except ValueError as exc:
            errors.append(f"component {uuid}: {exc}")
            continue
        triple = (system_node_id, node_id, "has_component")
        if triple not in touched_edges:
            touched_edges.add(triple)
            edges_added += 1
        try:
            upsert_edge(
                system_node_id, node_id, "has_component",
                metadata={SYSTEM_MODEL_ID_KEY: system_model_id},
            )
        except NodeNotFoundError as exc:
            errors.append(f"has_component edge for {uuid}: {exc}")

    # -- relationship pass: parent_of edges (depth >= 2) -----------------------
    if depth >= 2:
        for uuid, node_id in by_uuid.items():
            try:
                relationships = await client.call(
                    "get_component_relationships",
                    {"system_path": system_path, "component_id": uuid},
                )
                for parent_id, child_id in _parent_of_edges(by_uuid, uuid, relationships):
                    triple = (parent_id, child_id, "parent_of")
                    if triple in touched_edges:
                        continue
                    touched_edges.add(triple)
                    edges_added += 1
                    try:
                        upsert_edge(
                            parent_id, child_id, "parent_of",
                            metadata={SYSTEM_MODEL_ID_KEY: system_model_id},
                        )
                    except NodeNotFoundError as exc:
                        errors.append(f"parent_of edge {parent_id}->{child_id}: {exc}")
            except GdmClientError as exc:
                errors.append(f"relationships for {uuid}: {exc}")

    return {
        "success": True,
        "system_node_id": system_node_id,
        SYSTEM_MODEL_ID_KEY: system_model_id,
        "components_ingested": components_ingested,
        "edges_added": edges_added,
        "errors": errors,
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def ingest_components(
        system_path: str | None = None,
        model_id: str | None = None,
        depth: int = 2,
    ) -> str:
        """Ingest component nodes for a distribution system via the gdm MCP server.

        Resolves the system (exactly one of ``system_path``/``model_id``; a
        ``model_id`` is resolved via the model registry to its stored path),
        spawns gdm (``KG_GDM_COMMAND``/``KG_GDM_ARGS``), calls
        ``query_components``, and upserts ``component`` nodes plus
        ``has_component`` (system → component) edges. When ``depth >= 2``,
        ``get_component_relationships`` is also called per component to add
        ``parent_of`` (parent → child) edges.

        Args:
            system_path: Path to the distribution system JSON file.
            model_id: Registry model id resolved to a stored system path.
            depth: 1 = components + has_component only; ``>= 2`` also adds
                parent_of edges from the relationship pass (default 2).

        Returns:
            JSON ``{"success", "system_node_id", "system_model_id",
            "components_ingested", "edges_added", "errors"}``.
        """
        given = sum(
            1 for v in (system_path, model_id) if v is not None and v != ""
        )
        if given != 1:
            return error_payload(XOR_ERROR)
        try:
            depth = max(1, int(depth))
        except (TypeError, ValueError):
            return error_payload(f"depth must be a positive integer, got {depth!r}")
        try:
            get_kg_path()  # fail fast when no KG DB is configured
            report = await _ingest_components(
                system_path=system_path, model_id=model_id, depth=depth
            )
        except (KGUnavailableError, NodeNotFoundError, ValueError, GdmClientError) as exc:
            return error_payload(str(exc))
        return json_safe(report)
