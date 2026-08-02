"""A real MCPServer with scripted gdm inspection tools for Phase-3f tests.

Spawned through the production path (``kg_server.gdm_client``'s
``stdio_client``) — no real ``gdm`` package required. Returns deterministic
``query_components``/``get_component_relationships`` payloads mirroring the
real gdm tool shapes (``ComponentInfo``-style dicts with ``uuid``,
``component_type``, ``name``, ``substation``, ``feeder``, ``phases``,
``in_service``).
"""

from __future__ import annotations

from mcp.server import MCPServer

COMPONENTS = [
    {
        "uuid": "bus-1",
        "component_type": "DistributionBus",
        "name": "Bus 1",
        "substation": "Sub A",
        "feeder": "Feeder 1",
        "phases": ["A", "B", "C"],
        "in_service": True,
    },
    {
        "uuid": "load-1",
        "component_type": "DistributionLoad",
        "name": "Load 1",
        "substation": "Sub A",
        "feeder": "Feeder 1",
        "phases": ["A"],
        "in_service": True,
    },
    {
        "uuid": "solar-1",
        "component_type": "DistributionSolar",
        "name": "Solar 1",
        "substation": "Sub A",
        "feeder": "Feeder 1",
        "phases": ["A", "B"],
        "in_service": False,
    },
]

# parent_of (parent -> child): bus-1 is the parent of load-1 and solar-1.
# The fake reports the same structure from either endpoint (parents XOR children).
RELATIONSHIPS = {
    "bus-1": {"parents": [], "children": [COMPONENTS[1], COMPONENTS[2]]},
    "load-1": {"parents": [COMPONENTS[0]], "children": []},
    "solar-1": {"parents": [COMPONENTS[0]], "children": []},
}

# The system_path received by the tools, recorded for test assertions.
RECEIVED_SYSTEM_PATH: list[str] = []


def create_server() -> MCPServer:
    """Build the fake gdm server with scripted inspection tools."""
    mcp = MCPServer(
        "fake_gdm",
        version="0.0.0-fake",
        instructions="Scripted gdm server for dist-kg Phase-3f tests.",
    )

    @mcp.tool()
    def query_components(
        system_path: str | None = None,
        model_ref: dict | None = None,
    ) -> dict:
        """Return all scripted components."""
        RECEIVED_SYSTEM_PATH.append(system_path)
        return {"components": COMPONENTS, "count": len(COMPONENTS)}

    @mcp.tool()
    def get_component_relationships(
        system_path: str | None = None,
        model_ref: dict | None = None,
        *,
        component_id: str,
    ) -> dict:
        """Return scripted parents/children for a component id."""
        RECEIVED_SYSTEM_PATH.append(system_path)
        return RELATIONSHIPS.get(component_id, {"parents": [], "children": []})

    return mcp


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
