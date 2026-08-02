"""Ecosystem MCP server conventions home — no registry MCP server lives here (YAGNI).

``dist_stack.registry`` / ``dist_stack.manifest`` are the functional integration
points; this package only documents conventions and shares tiny helpers. See
``CONVENTIONS.md`` in this package for the ``register(mcp)`` / ``create_server()``
pattern used by the distribution-suite MCP servers.
"""

from __future__ import annotations

from .serialization import error_payload, json_safe

__all__ = ["json_safe", "error_payload"]
