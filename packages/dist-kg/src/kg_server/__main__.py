"""Entry point for the dist-kg MCP server.

Usage:
    kg-server
    python -m kg_server

Runs the :class:`mcp.server.MCPServer` over stdio.
"""

from __future__ import annotations

from kg_server.server import create_server


def main() -> None:
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
