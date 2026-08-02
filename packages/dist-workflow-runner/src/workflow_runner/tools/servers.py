"""Server introspection tools: ``list_servers``, ``list_tools``."""

from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

from workflow_runner.client import ServerError
from workflow_runner.models import AppContext


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def list_servers(ctx: Context[AppContext]) -> str:
        """List configured domain servers with connection status.

        Connects to each configured server and reports its reachability.

        Returns:
            JSON array of ``{"name", "status", "error"?, "tool_count",
            "server_version"}``.
        """
        app: AppContext = ctx.request_context.lifespan_context
        entries = []
        for name in app.pool.names:
            entry = {
                "name": name,
                "status": "connected",
                "error": None,
                "tool_count": 0,
                "server_version": None,
            }
            try:
                handle = await app.pool.connect(name)
                entry["server_version"] = handle.server_version
                entry["tool_count"] = len(await app.pool.list_tools(name))
            except Exception as exc:
                entry["status"] = "unavailable"
                entry["error"] = str(exc)
            entries.append(entry)
        return json.dumps(entries)

    @mcp.tool()
    async def list_tools(ctx: Context[AppContext], server: str) -> str:
        """List the tools a configured domain server exposes.

        Args:
            server: Name of the configured server (see ``list_servers``).

        Returns:
            JSON array of ``{"name", "description", "required_params"}``.
        """
        app: AppContext = ctx.request_context.lifespan_context
        try:
            tools = await app.pool.list_tools(server)
        except ServerError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        return json.dumps(tools)
