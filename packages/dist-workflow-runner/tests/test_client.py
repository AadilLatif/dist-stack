"""Client integration: real stdio spawn against tests/fake_server.py via the
production path (``stdio_client`` + ``ClientSession`` through ServerPool).

Each scenario binds the pool to a task group exactly like the production
lifespan does, and runs inside a SINGLE event loop.
"""

from __future__ import annotations

import asyncio
import sys

import anyio
import pytest

from conftest import FAKE_SERVER_PATH
from workflow_runner.client import (
    ServerConnectError,
    ToolCallTimeout,
    UnknownServerError,
    ServerPool,
)
from workflow_runner.models import ServerSpec


def _pool(timeout_s: int = 300) -> ServerPool:
    return ServerPool(
        [
            ServerSpec(
                name="fake_server",
                command=sys.executable,
                args=[str(FAKE_SERVER_PATH)],
                timeout_s=timeout_s,
            )
        ]
    )


def run(coro):
    """Run an async scenario to completion in a fresh event loop."""
    return asyncio.run(coro)


class TestSpawn:
    def test_connect_and_list_tools(self):
        async def scenario():
            pool = _pool()
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                tools = await pool.list_tools("fake_server")
                names = {t["name"] for t in tools}
                assert names == {"echo", "add", "fail_on_demand", "slow"}
                assert all(t["description"] for t in tools)
                echo = next(t for t in tools if t["name"] == "echo")
                assert "text" in echo["required_params"]
                await pool.close_all()

        run(scenario())

    def test_keep_alive_across_calls(self):
        """The spawned subprocess stays alive for multiple calls."""
        async def scenario():
            pool = _pool()
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                r1 = await pool.call_tool("fake_server", "echo", {"text": "one"})
                r2 = await pool.call_tool("fake_server", "add", {"a": 1, "b": 2})
                assert r1["text"] == "one"
                assert r2["sum"] == 3.0
                # Single handle held in the pool (one subprocess).
                assert len(pool._handles) == 1
                await pool.close_all()

        run(scenario())

    def test_server_version_reported(self):
        async def scenario():
            pool = _pool()
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                handle = await pool.connect("fake_server")
                assert handle.server_version == "0.0.0-fake"
                await pool.close_all()

        run(scenario())

    def test_unknown_server(self):
        async def scenario():
            pool = _pool()
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                with pytest.raises(UnknownServerError):
                    await pool.call_tool("nope", "echo", {"text": "x"})
                await pool.close_all()

        run(scenario())

    def test_connect_failure_is_server_connect_error(self):
        async def scenario():
            pool = ServerPool(
                [
                    ServerSpec(
                        name="missing",
                        command=sys.executable,
                        args=["-m", "no_such_module_xyz"],
                    )
                ]
            )
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                with pytest.raises(ServerConnectError):
                    await pool.connect("missing")
                await pool.close_all()

        run(scenario())

    def test_timeout_raises(self):
        """A call that exceeds the configured timeout raises ToolCallTimeout."""
        async def scenario():
            pool = _pool(timeout_s=1)
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                with pytest.raises(ToolCallTimeout, match="timed out"):
                    await pool.call_tool("fake_server", "slow", {"delay_s": 5.0})
                # Session survives a timed-out call.
                r = await pool.call_tool("fake_server", "echo", {"text": "still alive"})
                assert r["text"] == "still alive"
                await pool.close_all()

        run(scenario())
