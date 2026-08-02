"""Agent loop: the ~60-line turn that drives the LLM through its tools
(spec 15 §C.4). Pure async, no Streamlit.

A turn appends to the caller's ``messages`` list (OpenAI wire format) and
yields ``(assistant_message, turn_trace)`` after every tool round so the UI
can render progress. Async generators cannot return a value, so the
"budget exhausted" signal is written into an optional one-element
``stopped`` box the caller supplies: ``stopped[0]`` becomes ``True`` when
the max-turns budget ran out (the UI shows a "stopped" note) and ``False``
when the model answered normally.

Failure containment: the router never raises, so one failing or blocked tool
is fed back to the model as a tool result and the turn continues.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .llm import LLMClient, LLMResult
from .router import ToolRouter, TraceRecord

MAX_TURNS = 5
MAX_TOOL_RESULT_CHARS = 8000


def _assistant_message(result: LLMResult) -> dict[str, Any]:
    """The wire-format assistant message for a tool-calling result."""
    return {
        "role": "assistant",
        "content": result.text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in result.tool_calls
        ],
    }


async def agent_turn(
    messages: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    router: ToolRouter,
    llm: LLMClient,
    *,
    max_turns: int = MAX_TURNS,
    allow_write: bool = False,
    stopped: list[bool] | None = None,
) -> AsyncIterator[tuple[dict[str, Any], list[TraceRecord]]]:
    """Run the model↔tools loop until an answer or the turn budget.

    Yields ``(assistant_msg, turn_trace)`` per tool round (sequential calls).
    On completion, sets ``stopped[0]`` (when supplied) to ``True`` if
    ``max_turns`` rounds ran without a final text answer, else ``False``.
    """
    for _ in range(max_turns):
        result = await llm.complete(messages, catalog)
        if not result.tool_calls:
            messages.append({"role": "assistant", "content": result.text})
            if stopped is not None:
                stopped[0] = False
            return
        assistant_msg = _assistant_message(result)
        messages.append(assistant_msg)
        turn_trace: list[TraceRecord] = []
        for call in result.tool_calls:
            record = await router.execute(call, allow_write=allow_write)
            turn_trace.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": record.to_llm_content(MAX_TOOL_RESULT_CHARS),
                }
            )
        yield assistant_msg, turn_trace
    if stopped is not None:  # budget exhausted — the UI appends the note
        stopped[0] = True
