"""Integration: ToolRouter against a real MCPServer subprocess (spec 15 §G).

Spawns ``tests/fake_tool_server.py`` through the production path
(``ServerPool`` → ``dist_stack.mcp.client.session()``) exactly as the
dashboard does at runtime, then runs echo/add calls through the router. The
router's policy layer is exercised elsewhere; here the point is the transport:
mangled names resolve, results decode, and failures surface as records.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

import anyio

from workflow_runner.client import ServerPool
from workflow_runner.models import ServerSpec

from assistant import ToolRouter
from assistant.llm import LLMToolCall

FAKE_SERVER_PATH = Path(__file__).resolve().parent / "fake_tool_server.py"


def run(coro):
    return asyncio.run(coro)


class TestRouterIntegration(unittest.TestCase):
    def _pool(self) -> ServerPool:
        return ServerPool(
            [
                ServerSpec(
                    name="fake_server",
                    command=sys.executable,
                    args=[str(FAKE_SERVER_PATH)],
                    timeout_s=60,
                )
            ]
        )

    def test_echo_and_add_decode_through_router(self):
        async def scenario():
            pool = self._pool()
            router = ToolRouter(pool)
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                try:
                    echo = await router.execute(
                        LLMToolCall("call_1", "fake_server__echo", {"text": "hello"}),
                        allow_write=True,
                    )
                    add = await router.execute(
                        LLMToolCall("call_2", "fake_server__add", {"a": 2.0, "b": 3.0}),
                        allow_write=True,
                    )
                finally:
                    await pool.close_all()
            return echo, add

        echo, add = run(scenario())
        self.assertEqual(echo.status, "succeeded")
        self.assertEqual(echo.result, {"success": True, "text": "hello"})
        self.assertEqual(add.status, "succeeded")
        self.assertEqual(add.result["sum"], 5.0)
        self.assertGreater(echo.duration_ms, 0)

    def test_failure_payload_through_router(self):
        async def scenario():
            pool = self._pool()
            router = ToolRouter(pool)
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                try:
                    record = await router.execute(
                        LLMToolCall(
                            "call_1",
                            "fake_server__fail_on_demand",
                            {"note": "kaput"},
                        ),
                        allow_write=True,
                    )
                finally:
                    await pool.close_all()
            return record

        record = run(scenario())
        self.assertEqual(record.status, "failed")
        self.assertIn("kaput", record.error or "")

    def test_unknown_tool_returns_record_without_crashing(self):
        """An unknown tool must never crash the turn — the model reads the text."""
        async def scenario():
            pool = self._pool()
            router = ToolRouter(pool)
            async with anyio.create_task_group() as tg:
                pool.start(tg)
                try:
                    record = await router.execute(
                        LLMToolCall("call_1", "fake_server__no_such_tool", {}),
                        allow_write=True,
                    )
                finally:
                    await pool.close_all()
            return record

        record = run(scenario())
        self.assertIn(record.status, ("succeeded", "failed"))  # never raises
        self.assertIn("Unknown tool", record.to_llm_content())


if __name__ == "__main__":
    unittest.main()
