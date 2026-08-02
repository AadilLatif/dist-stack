"""Thin gdm MCP client wrapper over the shared ``dist_stack.mcp.client``.

dist-kg is stateless (no lifespan), so the gdm subprocess is spawned per call
and torn down in the **same task**. Spawn/init/teardown delegate to
``dist_stack.mcp.client.session()`` (which owns the anyio cancel-scope rules);
this module keeps the ``GdmClient``/``GdmClientError`` surface so
``kg_server.tools.components`` imports stay unchanged.

The gdm launch command comes from the environment:

- ``KG_GDM_COMMAND`` — the executable (default ``python``),
- ``KG_GDM_ARGS`` — space-separated arguments (default ``-m gdm.mcp.server``).

The child inherits the parent env, so ``DIST_STACK_MODEL_REGISTRY_DB`` etc.
flow through to gdm.
"""

from __future__ import annotations

import builtins
import os
import shlex
from typing import Any

import anyio

from dist_stack.mcp.client import ClientError, decode_result, session

DEFAULT_GDM_COMMAND = "python"
DEFAULT_GDM_ARGS = "-m gdm.mcp.server"
DEFAULT_TIMEOUT_S = 300

ENV_GDM_COMMAND = "KG_GDM_COMMAND"
ENV_GDM_ARGS = "KG_GDM_ARGS"


class GdmClientError(RuntimeError):
    """Base error for the gdm MCP client."""


class GdmConnectError(GdmClientError):
    """Failed to spawn/initialize the gdm subprocess."""


class GdmToolCallTimeout(GdmClientError):
    """A gdm tool call exceeded its timeout."""


def resolve_gdm_launch() -> list[str]:
    """``[command, *args]`` from ``KG_GDM_COMMAND``/``KG_GDM_ARGS``."""
    command = os.getenv(ENV_GDM_COMMAND, DEFAULT_GDM_COMMAND).strip()
    if not command:
        command = DEFAULT_GDM_COMMAND
    return [command, *shlex.split(os.getenv(ENV_GDM_ARGS, DEFAULT_GDM_ARGS))]


class GdmClient:
    """On-demand client for the gdm MCP server (spawns the subprocess per call).

    Usage::

        client = GdmClient()
        result = await client.call("query_components", {"system_path": path})
    """

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self.launch = resolve_gdm_launch()

    async def call(
        self, tool: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Spawn gdm, call ``tool``, close, and return the decoded JSON dict.

        Connection/initialization failures (including hangs) become
        :class:`GdmConnectError`; tool-call timeouts become
        :class:`GdmToolCallTimeout`. Payload-level ``{"success": False}``
        results are returned as-is for the caller to interpret.
        """
        try:
            with anyio.fail_after(self.timeout_s):
                async with session(self.launch, timeout_s=self.timeout_s) as conn:
                    try:
                        with anyio.fail_after(self.timeout_s):
                            result = await conn.call_tool(
                                tool, arguments=arguments or {}
                            )
                    except builtins.TimeoutError:
                        raise GdmToolCallTimeout(
                            f"gdm tool {tool!r} timed out after {self.timeout_s}s"
                        ) from None
                    return decode_result(result)
        except GdmClientError:
            raise
        except (ClientError, builtins.TimeoutError) as exc:
            raise GdmConnectError(
                f"failed to connect to gdm MCP server: {exc}"
            ) from exc
