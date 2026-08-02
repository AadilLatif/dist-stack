"""Tool router: per-call execution + trace records (spec 15 §C.5).

Every tool call the model makes goes through :class:`ToolRouter`, which is the
**second layer** of write enforcement: the catalog decides what the model can
see, and the router re-checks each call. A blocked or malformed call never
touches the pool — the model simply receives a ``blocked``/``failed`` trace
record as its tool result, so one bad call never aborts the turn.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .catalog import demangle
from .llm import LLMToolCall
from .policy import catalog_allowed

TRUNCATION_MARKER = "\n…[truncated]"


def truncate_text(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars, appending a marker when cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(TRUNCATION_MARKER))] + TRUNCATION_MARKER


@dataclass
class TraceRecord:
    """One tool call outcome, for both the UI and the model's eyes."""

    server: str
    tool: str
    arguments: dict[str, Any]
    status: str  # succeeded | failed | blocked
    error: str | None = None
    result: dict[str, Any] | None = None
    duration_ms: int = 0

    def to_llm_content(self, limit: int = 8000) -> str:
        """The ``role: "tool"`` message content the model sees.

        Compact JSON; tool results are data, never instructions (the model is
        told to treat them as untrusted). Truncated with a visible marker so a
        huge payload can't blow the context window.
        """
        payload = {
            "server": self.server,
            "tool": self.tool,
            "status": self.status,
            "error": self.error,
            "result": self.result,
        }
        return truncate_text(json.dumps(payload, default=str), limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "arguments": self.arguments,
            "status": self.status,
            "error": self.error,
            "result": self.result,
            "duration_ms": self.duration_ms,
        }


class ToolRouter:
    """Executes ``LLMToolCall`` objects against a server pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def execute(self, call: LLMToolCall, *, allow_write: bool) -> TraceRecord:
        """Resolve, policy-check, and run one tool call. Never raises.

        Malformed names and policy-blocked calls are resolved locally without
        touching the pool; transport/timeout failures become ``failed``
        records. A result with ``success: False`` is ``failed``; anything else
        is ``succeeded``.
        """
        try:
            server, tool = demangle(call.name)
        except ValueError as exc:
            return TraceRecord(
                server="",
                tool=call.name,
                arguments=call.arguments,
                status="failed",
                error=f"malformed tool name: {exc}",
            )

        if not catalog_allowed(server, tool, allow_write=allow_write):
            return TraceRecord(
                server=server,
                tool=tool,
                arguments=call.arguments,
                status="blocked",
                error="write tool blocked: read-only mode (enable write tools to allow)",
            )

        started = time.perf_counter()
        try:
            result = await self._pool.call_tool(server, tool, call.arguments)
        except Exception as exc:  # noqa: BLE001 — transport/timeout failures become records
            duration_ms = int((time.perf_counter() - started) * 1000)
            return TraceRecord(
                server=server,
                tool=tool,
                arguments=call.arguments,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        status = "failed" if result.get("success") is False else "succeeded"
        error: str | None = None
        if status == "failed" and result.get("error") is not None:
            raw_error = result["error"]
            error = raw_error if isinstance(raw_error, str) else json.dumps(raw_error, default=str)
        return TraceRecord(
            server=server,
            tool=tool,
            arguments=call.arguments,
            status=status,
            error=error,
            result=result,
            duration_ms=duration_ms,
        )
