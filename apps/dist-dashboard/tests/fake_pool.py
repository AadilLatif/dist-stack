"""In-memory ServerPool stand-in for assistant tests (spec 15 §G).

Mirrors the real :class:`workflow_runner.client.ServerPool` surface
(``names``/``connect``/``list_tools``/``call_tool``/``close_all``) plus the
status tracking the dashboard's :class:`assistant.PoolRuntime` adds. Copied
from the runner's ``tests/conftest.py`` pattern (the runner's conftest has no
``__init__.py`` so it is not importable here) and extended with
``input_schema`` output and per-server ``statuses``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import anyio

from workflow_runner.client import ToolCallTimeout, UnknownServerError


@dataclass
class FakeHandle:
    """Fake ``_ClientHandle`` — only ``server_version`` is read by tools."""

    name: str
    server_version: str = "0.0.0-test"


def _schema(fn: Callable) -> dict[str, Any]:
    """A minimal JSON schema derived from the function signature."""
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            is_number = param.annotation in (float, int) or "float" in str(param.annotation)
            properties[name] = {"type": "number" if is_number else "string"}
            if param.default is param.empty:
                required.append(name)
    return {"type": "object", "properties": properties, "required": required}


@dataclass
class FakePool:
    """Server name -> {tool name -> sync/async callable}, all in memory."""

    servers: dict[str, dict[str, Callable]] = field(default_factory=dict)
    connect_errors: set[str] = field(default_factory=set)
    calls: list[dict] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    default_timeout: float = 300.0
    closed: bool = False

    def add_server(self, name: str, tools: dict[str, Callable]) -> None:
        self.servers[name] = tools
        self.statuses[name] = "configured"

    @property
    def names(self) -> list[str]:
        return list(self.servers.keys())

    async def connect(self, name: str) -> FakeHandle:
        if name not in self.servers:
            raise UnknownServerError(f"no configured server named {name!r}")
        if name in self.connect_errors:
            self.statuses[name] = "error"
            raise RuntimeError(f"cannot spawn {name!r} (simulated spawn failure)")
        return FakeHandle(name)

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        if name not in self.servers:
            raise UnknownServerError(f"no configured server named {name!r}")
        try:
            await self.connect(name)
            tools = [
                {
                    "name": tool,
                    "description": (fn.__doc__ or "").strip(),
                    "required_params": list(_schema(fn).get("required", [])),
                    "input_schema": _schema(fn),
                }
                for tool, fn in self.servers[name].items()
            ]
        except Exception:
            self.statuses[name] = "error"
            raise
        self.statuses[name] = "connected"
        return tools

    async def call_tool(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        await self.connect(name)  # mirrors production: spawn/connect precedes the call
        if tool not in self.servers[name]:
            raise UnknownServerError(f"no tool {tool!r} on server {name!r}")
        timeout = timeout_s or self.default_timeout
        fn = self.servers[name][tool]
        self.calls.append({"server": name, "tool": tool, "arguments": arguments or {}})
        try:
            with anyio.fail_after(timeout):
                result = fn(**(arguments or {}))
                if asyncio.iscoroutine(result):
                    result = await result
        except TimeoutError:
            raise ToolCallTimeout(f"tool {tool!r} on server {name!r} timed out after {timeout}s") from None
        except Exception:
            self.statuses[name] = "error"
            raise
        self.statuses[name] = "connected"
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (TypeError, ValueError):
                return {"text": result}
        return result

    async def close_all(self) -> None:
        self.closed = True


def build_assistant_pool() -> FakePool:
    """A FakePool with the real workflow_runner + kg_server tool surfaces.

    Mirrors the tool sets curated in ``assistant.policy`` so read-only
    filtering behaves exactly like production would against live servers.
    """
    pool = FakePool()
    pool.add_server(
        "workflow_runner",
        {
            "list_runs": lambda limit=20: {"success": True, "runs": [{"run_id": "wf_abc"}]},
            "get_run": lambda run_id: {"success": True, "run_id": run_id},
            "list_servers": lambda: {"success": True, "servers": []},
            "list_tools": lambda server: {"success": True, "tools": []},
            "get_workflow": lambda workflow_id: {"success": True, "workflow_id": workflow_id},
            "list_workflows": lambda: {"success": True, "workflows": []},
            "run_workflow": lambda workflow_id, inputs={}: {
                "success": True,
                "run_id": "wf_exec",
                "workflow_id": workflow_id,
            },
            "create_workflow": lambda workflow_json: {"success": True, "workflow_id": "wf_new"},
        },
    )
    pool.add_server(
        "kg_server",
        {
            "search_nodes": lambda node_type=None, label=None: {
                "success": True,
                "nodes": [{"node_id": "n1", "node_type": node_type or "?"}],
            },
            "get_node": lambda node_id: {"success": True, "node_id": node_id},
            "get_neighbors": lambda node_id: {"success": True, "neighbors": []},
            "graph_stats": lambda: {"success": True, "nodes": 3, "edges": 2},
            "query_provenance": lambda run_id: {"success": False, "error": f"boom: {run_id}"},
            "get_provenance_chain": lambda run_id: {
                "success": True,
                "chain": [{"run_id": run_id}],
            },
            "ingest": lambda source="runstore": {"success": True, "ingested": 1},
            "ingest_components": lambda system_path: {"success": True, "components": 0},
            "echo_big": lambda text="x": {"success": True, "blob": text * 40000},
        },
    )
    return pool
