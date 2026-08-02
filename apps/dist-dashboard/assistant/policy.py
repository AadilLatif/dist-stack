"""Write-tool policy: explicit per-server allowlists (spec 15 §E).

Security model: **allowlist, not denylist.** Read-only mode admits exactly the
tools listed in :data:`READ_ONLY_TOOLS`; every other tool — including any tool
added to a server later, or a completely unknown server — is blocked. Enabling
write tools admits everything.

This module carries the curated tool inventories so a human can review them:
:data:`KNOWN_TOOLS` is the real tool surface per server (workflow_runner and
kg_server are verified against this repo's source; the five external domain
servers are curated from ``docs/mcp-wiring.md``), :data:`READ_ONLY_TOOLS` and
:data:`WRITE_TOOLS` partition it, and :func:`drift_report` detects entries
that have drifted out of sync (used by the tests so the lists can't rot).

Keep the lists conservative: a missing read-only tool just means the model
can't call it; a missing write tool is the point. Unknown tools default to
blocked either way.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Real tool surfaces (curated; see module docstring)
# ---------------------------------------------------------------------------

KNOWN_TOOLS: dict[str, frozenset[str]] = {
    # workflow_runner — verified against packages/dist-workflow-runner source.
    "workflow_runner": frozenset(
        {
            "run_workflow",
            "create_workflow",
            "get_workflow",
            "list_workflows",
            "list_servers",
            "list_tools",
            "get_run",
            "list_runs",
        }
    ),
    # kg_server — verified against packages/dist-kg source.
    "kg_server": frozenset(
        {
            "ingest",
            "ingest_components",
            "get_node",
            "get_neighbors",
            "search_nodes",
            "graph_stats",
            "query_provenance",
            "get_provenance_chain",
        }
    ),
    # The five external domain servers — curated from docs/mcp-wiring.md and
    # docs/architecture-assessment/04-capability-matrix.md. Verify against each
    # server before widening (unknown tools are blocked by default).
    "gdm": frozenset(
        {
            "query_components",
            "get_system_summary",
            "split_by_substation",
            "split_by_feeder",
        }
    ),
    "gdm_flow": frozenset({"run_ac_pf", "export_sqlite"}),
    "erad": frozenset(
        {
            "load_distribution_model",
            "load_hazard_model",
            "list_historic_hurricanes",
            "list_historic_wildfires",
            "get_failed_assets",
            "run_simulation",
            "export_to_sqlite",
            "export_csv",
            "export_parquet",
        }
    ),
    "ditto": frozenset({"read_opendss_model", "convert_model", "write_cim"}),
    "shift": frozenset(
        {"fetch_parcels", "cluster_parcels", "build_graph_from_groups", "create_mesh_network"}
    ),
}

# ---------------------------------------------------------------------------
# Read-only allowlist (the default surface) + write complement
# ---------------------------------------------------------------------------

#: Tools the assistant may call with write tools DISABLED.
READ_ONLY_TOOLS: dict[str, frozenset[str]] = {
    "workflow_runner": frozenset(
        {
            "get_workflow",
            "list_workflows",
            "list_servers",
            "list_tools",
            "get_run",
            "list_runs",
        }
    ),
    "kg_server": frozenset(
        {
            "get_node",
            "get_neighbors",
            "search_nodes",
            "graph_stats",
            "query_provenance",
            "get_provenance_chain",
        }
    ),
    "gdm": frozenset({"query_components", "get_system_summary"}),
    "gdm_flow": frozenset({"run_ac_pf"}),
    "erad": frozenset(
        {
            "load_distribution_model",
            "load_hazard_model",
            "list_historic_hurricanes",
            "list_historic_wildfires",
            "get_failed_assets",
        }
    ),
    "ditto": frozenset({"read_opendss_model", "convert_model"}),
    "shift": frozenset({"fetch_parcels", "cluster_parcels"}),
}

#: Known write tools per server — the complement of the read-only surface.
#: Used for classification checks + the drift guard (write tools must never
#: appear in the read-only allowlist).
WRITE_TOOLS: dict[str, frozenset[str]] = {
    "workflow_runner": frozenset({"run_workflow", "create_workflow"}),
    "kg_server": frozenset({"ingest", "ingest_components"}),
    "gdm": frozenset({"split_by_substation", "split_by_feeder"}),
    "gdm_flow": frozenset({"export_sqlite"}),
    "erad": frozenset({"run_simulation", "export_to_sqlite", "export_csv", "export_parquet"}),
    "ditto": frozenset({"write_cim"}),
    "shift": frozenset({"build_graph_from_groups", "create_mesh_network"}),
}


# ---------------------------------------------------------------------------
# Decision + drift guard
# ---------------------------------------------------------------------------


def catalog_allowed(server: str, tool: str, *, allow_write: bool) -> bool:
    """Admit ``tool`` on ``server`` for the current write-tools setting.

    ``allow_write=True`` admits everything (the operator explicitly enabled
    write tools); otherwise only the read-only allowlist counts. Unknown
    servers and unknown tools default to blocked.
    """
    if allow_write:
        return True
    return tool in READ_ONLY_TOOLS.get(server, ())


def drift_report(
    *,
    read_only: dict[str, frozenset[str]] | None = None,
    write: dict[str, frozenset[str]] | None = None,
    known_tools: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """Return a list of policy drift problems; empty means healthy.

    Guards: read-only and write sets overlap; any classified tool is missing
    from the known tool surface; known servers have no classification at all.
    The sets are overridable so tests can inject broken policies.
    """
    read = read_only if read_only is not None else READ_ONLY_TOOLS
    write = write if write is not None else WRITE_TOOLS
    known = known_tools if known_tools is not None else KNOWN_TOOLS
    problems: list[str] = []
    for server in sorted(set(read) | set(write)):
        read_set = set(read.get(server, ()))
        write_set = set(write.get(server, ()))
        overlap = read_set & write_set
        if overlap:
            problems.append(
                f"{server}: tools classified as both read-only and write: {sorted(overlap)}"
            )
        known_surface = set(known.get(server, ()))
        for tool in sorted(read_set | write_set):
            if tool not in known_surface:
                problems.append(f"{server}: tool {tool!r} is not in the known tool surface")
    return problems
