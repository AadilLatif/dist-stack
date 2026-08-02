"""Shared low-level MCP stdio client for the distribution-suite orchestration plane.

One subprocess per :func:`session` enter, entered and exited in the **same
task** (the anyio cancel-scope rule). ``dist-kg`` wraps this per-call
(``kg_server.gdm_client``); the workflow-runner's ``ServerPool`` keeps
connections alive across calls by owning the context manager in a long-lived
task.

Contract::

    async with session([python, "-m", "gdm.mcp.server"], timeout_s=60) as s:
        result = await s.call_tool("query_components", arguments={...})

Failures: spawn/initialize problems raise :class:`ConnectError`; an initialize
that exceeds ``timeout_s`` raises :class:`TimeoutError`.
"""

from __future__ import annotations

import builtins
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


class ClientError(RuntimeError):
    """Base error for the shared MCP client."""


class ConnectError(ClientError):
    """Failed to spawn/initialize the subprocess session."""


class TimeoutError(ClientError):
    """A connection (initialize) exceeded its timeout."""


@asynccontextmanager
async def session(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_s: float = 300,
    cwd: str | None = None,
) -> AsyncIterator[ClientSession]:
    """Spawn ``command`` over stdio and yield an initialized ``ClientSession``.

    ``command`` is ``[executable, *args]``; ``env``/``cwd`` are passed through
    to the subprocess (the SDK merges ``env`` with the parent environment).
    Enter/exit MUST happen in the same task — the anyio cancel-scope rule.
    """
    if not command:
        raise ConnectError("session() requires a non-empty command list")

    stdio_cm = stdio_client(
        StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=cwd)
    )
    try:
        read_stream, write_stream = await stdio_cm.__aenter__()
    except Exception as exc:
        raise ConnectError(
            f"failed to spawn MCP subprocess {command[0]!r}: {exc}"
        ) from exc

    session_cm: Any = None
    try:
        session_cm = ClientSession(read_stream, write_stream)
        session = await session_cm.__aenter__()
        try:
            with anyio.fail_after(timeout_s):
                await session.initialize()
        except builtins.TimeoutError as exc:
            raise TimeoutError(
                f"failed to initialize MCP session within {timeout_s}s"
            ) from exc
        except Exception as exc:
            raise ConnectError(f"failed to initialize MCP session: {exc}") from exc
        yield session
    finally:
        if session_cm is not None:
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
        try:
            await stdio_cm.__aexit__(None, None, None)
        except Exception:
            pass


def decode_result(result: Any) -> dict[str, Any]:
    """Decode a ``CallToolResult`` into a JSON-friendly dict.

    ``structuredContent`` wins when present; otherwise the concatenated text
    content is JSON-decoded (the ecosystem JSON-string convention). An
    ``isError`` result becomes ``{"success": False, "error": ...}`` so callers
    can treat transport- and payload-level failures uniformly.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.loads(json.dumps(structured, default=str))

    is_error = bool(getattr(result, "isError", False))
    texts = [
        item.text
        for item in (getattr(result, "content", []) or [])
        if getattr(item, "type", None) == "text" and getattr(item, "text", None)
    ]
    raw = "\n".join(texts).strip()
    if is_error:
        return {"success": False, "error": raw or "domain server returned an error"}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"text": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}
