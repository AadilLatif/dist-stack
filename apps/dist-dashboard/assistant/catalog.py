"""Tool catalog: mangle/demangle + building the LLM tool array (spec 15 §C.3).

The model only ever sees ``server__tool`` names; the catalog maps those back
to a real server/tool pair at execution time. ``mangle``/``demangle`` are
pure string helpers; ``build_catalog`` walks every configured server through
``pool.list_tools`` and filters each tool through ``policy.catalog_allowed``
(one half of the two-layer write enforcement).
"""

from __future__ import annotations

from typing import Any

from .policy import catalog_allowed

SEPARATOR = "__"


def mangle(server: str, tool: str) -> str:
    """Join a server and tool name into a single catalog entry name."""
    return f"{server}{SEPARATOR}{tool}"


def demangle(name: str) -> tuple[str, str]:
    """Split a catalog entry name back into ``(server, tool)``.

    Raises ``ValueError`` when the name has no ``__`` separator or either
    part is empty. Server names must not contain ``__``.
    """
    parts = name.split(SEPARATOR, 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"malformed tool name {name!r}: expected 'server__tool'"
        )
    return parts[0], parts[1]


async def build_catalog(
    pool: Any,
    server_names: list[str],
    *,
    allow_write: bool,
) -> list[dict[str, Any]]:
    """Build the OpenAI ``tools`` array from every configured server.

    For each server: ``list_tools``, then keep only tools that
    :func:`policy.catalog_allowed` admits. Connect failures skip the server
    (the pool records its status as ``error`` — the catalog never crashes the
    assistant on a dead server). Entry names are mangled and descriptions are
    prefixed with ``[server]`` and capped at 1024 chars.
    """
    catalog: list[dict[str, Any]] = []
    for server in server_names:
        try:
            tools = await pool.list_tools(server)
        except Exception:  # noqa: BLE001 — dead servers are skipped, not fatal
            continue
        for tool in tools:
            name = tool.get("name") or ""
            if not name or not catalog_allowed(server, name, allow_write=allow_write):
                continue
            description = tool.get("description") or ""
            schema = tool.get("input_schema") or {}
            catalog.append(
                {
                    "type": "function",
                    "function": {
                        "name": mangle(server, name),
                        "description": f"[{server}] {description}"[:1024],
                        "parameters": schema,
                    },
                }
            )
    return catalog
