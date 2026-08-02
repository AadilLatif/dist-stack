"""A real MCPServer with scripted tools for the client/integration tests (§2.8).

Spawned via the production path (``stdio_client`` + ``ClientSession``) by
``tests/test_client.py`` and ``tests/test_integration.py``.
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
        instructions="Scripted test server for dist-workflow-runner.",
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
