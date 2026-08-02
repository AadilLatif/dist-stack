"""Shared fixtures: FakePool, tmp runstore DB, tmp workflow dir, MockContext.

``FakePool`` mirrors the production :class:`workflow_runner.client.ServerPool`
interface (``connect``/``list_tools``/``call_tool``/``close_all``) without
spawning subprocesses; ``call_tool`` enforces the timeout with
``anyio.fail_after`` exactly like the real pool.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import anyio
import pytest

from workflow_runner.client import ToolCallTimeout, UnknownServerError
from workflow_runner.models import AppContext, RunnerConfig, ServerSpec

FAKE_SERVER_PATH = Path(__file__).resolve().parent / "fake_server.py"


@dataclass
class FakeHandle:
    """Fake ``_ClientHandle`` — only ``server_version`` is read by tools."""

    name: str
    server_version: str = "0.0.0-test"


@dataclass
class FakePool:
    """In-memory ServerPool stand-in with the same call interface.

    ``servers`` maps server name -> {tool name -> sync/async callable}.
    ``connect_errors`` names servers whose ``connect`` raises (for the
    list_servers "unavailable" test).
    """

    servers: dict[str, dict[str, Callable]] = field(default_factory=dict)
    connect_errors: set[str] = field(default_factory=set)
    calls: list[dict] = field(default_factory=list)
    default_timeout: float = 300.0
    closed: bool = False

    def add_server(self, name: str, tools: dict[str, Callable]) -> None:
        self.servers[name] = tools

    @property
    def names(self) -> list[str]:
        return list(self.servers.keys())

    async def connect(self, name: str) -> FakeHandle:
        if name not in self.servers:
            raise UnknownServerError(f"no configured server named {name!r}")
        if name in self.connect_errors:
            raise RuntimeError(f"cannot spawn {name!r} (simulated spawn failure)")
        return FakeHandle(name)

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        if name not in self.servers:
            raise UnknownServerError(f"no configured server named {name!r}")
        return [
            {
                "name": tool,
                "description": (fn.__doc__ or "").strip(),
                "required_params": [],
            }
            for tool, fn in self.servers[name].items()
        ]

    async def call_tool(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        if name not in self.servers:
            raise UnknownServerError(f"no configured server named {name!r}")
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
            raise ToolCallTimeout(
                f"tool {tool!r} on server {name!r} timed out after {timeout}s"
            ) from None
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (TypeError, ValueError):
                return {"text": result}
        return result

    async def close_all(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Mock MCP Context (doc-10 direct-function-call pattern)
# ---------------------------------------------------------------------------


@dataclass
class _MockRequestContext:
    lifespan_context: AppContext


class MockContext:
    """Mimics the MCP Context for direct tool-function calls.

    Usage::
        fn = mcp._tool_manager._tools["run_workflow"].fn
        result = await fn(MockContext(app), workflow_id="...", inputs={...})
    """

    def __init__(self, app: AppContext) -> None:
        self.request_context = _MockRequestContext(lifespan_context=app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pool() -> FakePool:
    """A FakePool with the fake_server tools (echo/add/fail_on_demand/...)."""
    pool = FakePool()
    pool.add_server(
        "fake_server",
        {
            "echo": lambda text: {"success": True, "text": text},
            "add": lambda a, b: {"success": True, "a": a, "b": b, "sum": float(a) + float(b)},
            "fail_on_demand": lambda should_fail=True, note="deliberate failure": (
                {"success": False, "error": note} if should_fail else {"success": True, "note": note}
            ),
            "get_system_summary": lambda system_path: {
                "success": True,
                "system": system_path,
                "summary": {"buses": 1, "branches": 2},
            },
            "run_ac_pf": lambda system_path, include_details=True: {
                "success": True,
                "system": system_path,
                "details": include_details,
                "vmag": [1.0, 0.98],
            },
        },
    )
    return pool


@pytest.fixture
def runstore_db(tmp_path) -> Path:
    """A fresh, empty runstore DB path (schema created on first use)."""
    return tmp_path / "runstore.db"


@pytest.fixture
def workflow_dir(tmp_path) -> Path:
    """A fresh tmp workflow directory."""
    d = tmp_path / "workflows"
    d.mkdir()
    return d


@pytest.fixture
def app_ctx(fake_pool: FakePool, runstore_db: Path, workflow_dir: Path) -> AppContext:
    """An AppContext wiring the FakePool to tmp runstore + workflow dirs."""
    config = RunnerConfig(
        runstore_db=str(runstore_db),
        workflow_dir=str(workflow_dir),
        servers=[ServerSpec(name="fake_server", command=sys.executable)],
    )
    return AppContext(config=config, pool=fake_pool)


@pytest.fixture
def mock_ctx(app_ctx: AppContext) -> MockContext:
    return MockContext(app_ctx)


def run(coro) -> Any:
    """Run an async coroutine to completion (avoids pytest-asyncio)."""
    return asyncio.run(coro)
