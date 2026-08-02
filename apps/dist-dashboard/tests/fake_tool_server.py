"""A real MCPServer with scripted tools for the assistant tests (spec 15 §G).

Spawned as a stdio subprocess by ``test_router_integration`` through the
production path (``dist_stack.mcp.client.session()`` via ``ServerPool``) —
the same route the dashboard uses at runtime.
"""

from __future__ import annotations

import asyncio
import json

from mcp.server import MCPServer


def create_server() -> MCPServer:
    """Build the fake domain server with echo/add/fail_on_demand/slow tools."""
    mcp = MCPServer(
        "fake_server",
        version="0.0.0-fake",
        instructions="Scripted test server for the dist-dashboard assistant.",
    )

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo the input text back."""
        return json.dumps({"success": True, "text": text})

    @mcp.tool()
    def add(a: float, b: float) -> str:
        """Add two numbers."""
        return json.dumps({"success": True, "a": a, "b": b, "sum": a + b})

    @mcp.tool()
    def fail_on_demand(should_fail: bool = True, note: str = "deliberate failure") -> str:
        """Return an error payload on demand."""
        if should_fail:
            return json.dumps({"success": False, "error": note})
        return json.dumps({"success": True, "note": note})

    @mcp.tool()
    async def slow(delay_s: float = 5.0) -> str:
        """Sleep for delay_s seconds, then succeed."""
        await asyncio.sleep(delay_s)
        return json.dumps({"success": True, "slept": delay_s})

    return mcp


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
