"""Tests for the agent loop (spec 15 §G): agent_turn end-to-end with fakes.

Covers direct answers, one/two tool rounds, the max-turns guard, failing-tool
recovery, blocked writes, result truncation and tool_call_id bookkeeping.
"""

from __future__ import annotations

import asyncio
import unittest

from assistant import MAX_TOOL_RESULT_CHARS, MAX_TURNS, ToolRouter, agent_turn, build_catalog
from assistant.llm import LLMResult, LLMToolCall
from fake_llm import FakeLLM
from fake_pool import build_assistant_pool


def run(coro):
    return asyncio.run(coro)


async def run_turn(messages, catalog, router, llm, **kwargs):
    """Consume the agent_turn generator; returns (events, stopped)."""
    stopped_box: list[bool] = [False]
    kwargs.setdefault("stopped", stopped_box)
    gen = agent_turn(messages, catalog, router, llm, **kwargs)
    events = []
    try:
        while True:
            msg, trace = await gen.__anext__()
            events.append((msg, trace))
    except StopAsyncIteration:
        pass
    return events, stopped_box[0]


def llm_call(call_id: str, name: str, arguments: dict | None = None) -> LLMToolCall:
    return LLMToolCall(call_id, name, arguments or {})


class TestAgentTurn(unittest.TestCase):
    def _setup(self, script, *, allow_write=False, max_turns=MAX_TURNS):
        pool = build_assistant_pool()
        catalog = run(build_catalog(pool, pool.names, allow_write=allow_write))
        router = ToolRouter(pool)
        llm = FakeLLM(list(script))
        return pool, catalog, router, llm, allow_write, max_turns

    def test_direct_answer_no_tools(self):
        pool, catalog, router, llm, allow_write, _ = self._setup(
            [LLMResult("Hello! I can help.", ())]
        )
        messages = [{"role": "user", "content": "hi"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(events, [])
        self.assertFalse(stopped)
        self.assertEqual(messages[-1], {"role": "assistant", "content": "Hello! I can help."})
        self.assertEqual(len(pool.calls), 0)

    def test_one_tool_round(self):
        pool, catalog, router, llm, allow_write, _ = self._setup(
            [
                LLMResult("", (llm_call("call_01", "kg_server__search_nodes", {"node_type": "artifact"}),)),
                LLMResult("Found 3 artifact nodes.", ()),
            ]
        )
        messages = [{"role": "user", "content": "how many artifact nodes?"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(len(events), 1)
        self.assertFalse(stopped)
        self.assertEqual(len(pool.calls), 1)
        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(messages[-1]["content"], "Found 3 artifact nodes.")

    def test_two_round_chain(self):
        script = [
            LLMResult("", (llm_call("call_01", "kg_server__search_nodes", {"node_type": "artifact"}),)),
            LLMResult("", (llm_call("call_02", "kg_server__graph_stats", {}),)),
            LLMResult("The graph holds 3 nodes and 2 edges.", ()),
        ]
        pool, catalog, router, llm, allow_write, _ = self._setup(script)
        messages = [{"role": "user", "content": "summarise the graph"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(len(events), 2)  # one yield per tool round
        self.assertFalse(stopped)
        self.assertEqual([t.status for _m, trace in events for t in trace],
                         ["succeeded", "succeeded"])
        self.assertEqual(len(pool.calls), 2)

    def test_max_turns_guard(self):
        # 6 tool rounds scripted; the loop must stop at max_turns=5.
        script = [
            LLMResult("", (llm_call(f"call_{i:02d}", "kg_server__search_nodes", {}),))
            for i in range(6)
        ]
        pool, catalog, router, llm, allow_write, max_turns = self._setup(script, max_turns=5)
        messages = [{"role": "user", "content": "keep going"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertTrue(stopped)
        self.assertEqual(len(events), 5)
        self.assertLessEqual(len(pool.calls), 5)  # 6th round never ran
        self.assertEqual(len(llm.script), 1)  # the 6th scripted result was never consumed
        self.assertNotEqual(messages[-1]["role"], "assistant")  # no final answer

    def test_failing_tool_fed_back_and_model_recovers(self):
        pool, catalog, router, llm, allow_write, _ = self._setup(
            [
                LLMResult("", (llm_call("call_01", "kg_server__query_provenance", {"run_id": "wf_bad"}),)),
                LLMResult("That lookup failed — the error says boom.", ()),
            ]
        )
        messages = [{"role": "user", "content": "provenance for wf_bad"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(len(events), 1)
        self.assertFalse(stopped)
        tool_msg = next(m for m in messages if m["role"] == "tool")
        self.assertIn("boom", tool_msg["content"])  # error fed back to the model
        self.assertEqual(messages[-1]["content"], "That lookup failed — the error says boom.")

    def test_blocked_write(self):
        pool, catalog, router, llm, allow_write, _ = self._setup(
            [
                LLMResult("", (llm_call("call_01", "workflow_runner__run_workflow", {"workflow_id": "wf_x"}),)),
                LLMResult("I can't run workflows in read-only mode.", ()),
            ]
        )
        messages = [{"role": "user", "content": "run workflow wf_x"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(len(events), 1)
        self.assertFalse(stopped)
        self.assertEqual(len(pool.calls), 0)  # the pool was never invoked
        tool_msg = next(m for m in messages if m["role"] == "tool")
        self.assertIn("blocked", tool_msg["content"])
        self.assertEqual(events[0][1][0].status, "blocked")

    def test_truncation_at_8000_chars(self):
        # echo_big returns a ~40KB blob; allow_write=True so the call runs.
        pool, catalog, router, llm, allow_write, _ = self._setup(
            [
                LLMResult("", (llm_call("call_01", "kg_server__echo_big", {"text": "x"}),)),
                LLMResult("Received the blob.", ()),
            ],
            allow_write=True,
        )
        messages = [{"role": "user", "content": "give me a big blob"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertFalse(stopped)
        tool_msg = next(m for m in messages if m["role"] == "tool")
        self.assertLessEqual(len(tool_msg["content"]), MAX_TOOL_RESULT_CHARS)
        self.assertTrue(tool_msg["content"].endswith("[truncated]"))

    def test_tool_call_id_bookkeeping(self):
        script = [
            LLMResult(
                "",
                (
                    llm_call("call_aa", "kg_server__search_nodes", {}),
                    llm_call("call_bb", "kg_server__graph_stats", {}),
                ),
            ),
            LLMResult("Done.", ()),
        ]
        pool, catalog, router, llm, allow_write, _ = self._setup(script)
        messages = [{"role": "user", "content": "multi call"}]
        events, stopped = run(run_turn(messages, catalog, router, llm, allow_write=allow_write))
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0][1]), 2)
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual({m["tool_call_id"] for m in tool_msgs}, {"call_aa", "call_bb"})
        # the assistant message carries the exact wire-format tool_calls
        assistant_msg = events[0][0]
        self.assertEqual([tc["id"] for tc in assistant_msg["tool_calls"]], ["call_aa", "call_bb"])
        self.assertEqual(assistant_msg["tool_calls"][0]["function"]["name"], "kg_server__search_nodes")
        self.assertEqual(assistant_msg["content"], None)


if __name__ == "__main__":
    unittest.main()
