"""ServerPool lifecycle for Streamlit (spec 15 §B).

The MCP stdio client requires its anyio cancel scopes to be entered and
exited in the same long-lived task, but Streamlit reruns the script
top-to-bottom per interaction. :class:`PoolRuntime` solves this with one
daemon thread that owns an asyncio loop + anyio task group + the real
:class:`ServerPool`: the agent loop (in the script thread) crosses the thread
boundary only through :meth:`call`/``call_tool``/``list_tools``.

Lifecycle: created lazily on first assistant render, torn down when the
servers.yaml path changes, via "Restart connections", or at process exit (the
thread is a daemon).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

import anyio

from workflow_runner.client import ServerPool
from workflow_runner.models import ServerSpec


class PoolRuntimeError(RuntimeError):
    """Raised when a pool operation runs while the runtime is down."""


class PoolRuntime:
    """A ServerPool kept alive on a dedicated thread."""

    def __init__(self, specs: list[ServerSpec]) -> None:
        self._specs = specs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pool: ServerPool | None = None
        self._shutdown: asyncio.Event | None = None
        self._started = threading.Event()
        self._statuses: dict[str, str] = {s.name: "configured" for s in specs}
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Spawn the daemon thread and wait until the pool is bound."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="assistant-pool", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=10.0):
            raise PoolRuntimeError("pool runtime failed to start within 10s")

    def stop(self) -> None:
        """Signal shutdown and join the thread (timeout 5s)."""
        shutdown, self._shutdown = self._shutdown, None
        loop = self._loop
        if shutdown is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(shutdown.set(), loop)
            except Exception:  # pragma: no cover — loop may be closing
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)
        self._loop = None
        self._pool = None

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._specs]

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._amain())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover
                pass
            loop.close()

    async def _amain(self) -> None:
        pool = ServerPool(self._specs)
        self._pool = pool
        self._shutdown = asyncio.Event()
        async with anyio.create_task_group() as tg:
            pool.start(tg)
            self._started.set()
            await self._shutdown.wait()
        # task-group exit tears down every connection (owner tasks joined)

    # -- thread boundary ----------------------------------------------------

    def call(self, fn: Callable[..., Any], *args: Any, timeout_s: float = 300.0) -> Any:
        """Run ``fn(*args)`` (an async callable) on the pool loop."""
        loop = self._loop
        if loop is None or not self.is_running:
            raise PoolRuntimeError("pool runtime is not running")
        future = asyncio.run_coroutine_threadsafe(fn(*args), loop)
        return future.result(timeout=timeout_s)

    # -- pool surface (run via call / used from the agent loop) -------------

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        pool = self._pool
        if pool is None:
            raise PoolRuntimeError("pool runtime is not running")
        try:
            tools = await pool.list_tools(name)
        except Exception:
            self._set_status(name, "error")
            raise
        self._set_status(name, "connected")
        return tools

    async def call_tool(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        pool = self._pool
        if pool is None:
            raise PoolRuntimeError("pool runtime is not running")
        try:
            result = await pool.call_tool(name, tool, arguments, timeout_s=timeout_s)
        except Exception:
            self._set_status(name, "error")
            raise
        self._set_status(name, "connected")
        return result

    # -- status -------------------------------------------------------------

    def statuses(self) -> dict[str, str]:
        """Current per-server status: ``configured|connected|error``."""
        with self._lock:
            return dict(self._statuses)

    def check_all(self, per_server_timeout_s: int = 20) -> dict[str, str]:
        """Connect to every server (via the pool loop) and report statuses."""
        return self.call(self._check_all, per_server_timeout_s=per_server_timeout_s)

    async def _check_all(self, per_server_timeout_s: int) -> dict[str, str]:
        for name in self.names:
            try:
                await self.list_tools(name)
            except Exception:  # noqa: BLE001 — statuses() carries the failure
                pass
        return self.statuses()

    def _set_status(self, name: str, status: str) -> None:
        with self._lock:
            self._statuses[name] = status
