"""Tests for ToolRouter.execute (spec 15 §G).

Covers success, payload-level failure, transport/timeout failure, the blocked
write path (which must never touch the pool), and malformed names.
"""

from __future__ import annotations

import asyncio
import unittest

from workflow_runner.client import ToolCallTimeout

from assistant import ToolRouter
from assistant.llm import LLMToolCall
from fake_pool import FakePool, build_assistant_pool


def run(coro):
    return asyncio.run(coro)


class TestToolRouter(unittest.TestCase):
    def _router(self, pool):
        return ToolRouter(pool)

    def test_success(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "kg_server__search_nodes", {"node_type": "artifact"})
        record = run(router.execute(call, allow_write=False))
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.server, "kg_server")
        self.assertEqual(record.tool, "search_nodes")
        self.assertEqual(record.result["success"], True)
        self.assertEqual(pool.calls, [{"server": "kg_server", "tool": "search_nodes",
                                       "arguments": {"node_type": "artifact"}}])

    def test_failure_payload(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "kg_server__query_provenance", {"run_id": "wf_bad"})
        record = run(router.execute(call, allow_write=False))
        self.assertEqual(record.status, "failed")
        self.assertIn("boom", record.error or "")
        self.assertIn("wf_bad", record.error or "")

    def test_timeout_becomes_failed(self):
        def _timed_out(**kwargs):  # noqa: ARG001
            raise ToolCallTimeout("tool 'slow' on server 'kg_server' timed out after 1s")

        pool = FakePool()
        pool.add_server("kg_server", {"slow": _timed_out})
        router = self._router(pool)
        call = LLMToolCall("call_1", "kg_server__slow", {})
        record = run(router.execute(call, allow_write=True))
        self.assertEqual(record.status, "failed")
        self.assertIn("timed out", record.error or "")

    def test_transport_error_becomes_failed(self):
        pool = build_assistant_pool()
        pool.connect_errors.add("workflow_runner")
        router = self._router(pool)
        call = LLMToolCall("call_1", "workflow_runner__list_runs", {})
        record = run(router.execute(call, allow_write=False))
        self.assertEqual(record.status, "failed")
        self.assertIn("cannot spawn", record.error or "")

    def test_blocked_write_never_touches_pool(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "workflow_runner__run_workflow", {"workflow_id": "wf_x"})
        record = run(router.execute(call, allow_write=False))
        self.assertEqual(record.status, "blocked")
        self.assertEqual(pool.calls, [])  # the pool was never invoked
        self.assertIn("read-only", record.error or "")

    def test_allow_write_runs_the_same_tool(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "workflow_runner__run_workflow", {"workflow_id": "wf_x"})
        record = run(router.execute(call, allow_write=True))
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(len(pool.calls), 1)

    def test_malformed_name_becomes_failed_without_pool_touch(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "no_separator_here", {})
        record = run(router.execute(call, allow_write=False))
        self.assertEqual(record.status, "failed")
        self.assertIn("malformed", record.error or "")
        self.assertEqual(pool.calls, [])

    def test_record_to_dict_round_trip(self):
        pool = build_assistant_pool()
        router = self._router(pool)
        call = LLMToolCall("call_1", "kg_server__search_nodes", {})
        record = run(router.execute(call, allow_write=False))
        d = record.to_dict()
        self.assertEqual(d["status"], "succeeded")
        self.assertEqual(d["server"], "kg_server")
        self.assertIn("duration_ms", d)


if __name__ == "__main__":
    unittest.main()
