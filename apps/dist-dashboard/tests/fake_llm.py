"""Fake LLM for chat tests (spec 15 §G).

``FakeLLM(script)`` returns one pre-built :class:`LLMResult` per call, in
order, records a snapshot of every call (messages + tools + temperature), and
raises ``AssertionError`` when the script runs dry — so a test that loops
longer than expected fails loudly instead of silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from assistant.llm import LLMResult


@dataclass
class FakeLLM:
    """Scripted ``LLMClient`` stand-in with the same ``complete`` surface."""

    script: list[LLMResult]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> LLMResult:
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": list(tools or []),
                "temperature": temperature,
            }
        )
        if not self.script:
            raise AssertionError("FakeLLM script exhausted — the loop ran too long")
        return self.script.pop(0)
