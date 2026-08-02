"""Client layer: lazy, keep-alive stdio pool of domain MCP servers.

One subprocess per :class:`~workflow_runner.models.ServerSpec`, spawned **lazily
on first use** via the production SDK path (``stdio_client`` +
``ClientSession``) and kept alive for the runner process lifetime. Torn down in
the MCPServer lifespan teardown via :meth:`ServerPool.close_all`.

**Ownership model.** ``stdio_client`` / ``ClientSession`` enter anyio
task-group cancel scopes. Those scopes must be entered *and exited* in the same
long-lived task, otherwise the host task's cancel-scope stack is corrupted and
mcp's per-request handler scope can no longer exit cleanly. Each connection is
therefore owned by a dedicated "owner" task spawned into the pool's task group
(:meth:`ServerPool.start`); tool calls are performed cross-task on the session,
which is safe, while the owner task lives for the connection's lifetime and
performs the teardown. Spawn/init/teardown delegate to the shared
``dist_stack.mcp.client.session()``.

Env inheritance is handled by the SDK (``get_default_environment() | env``), so
``config.env`` is where ``DIST_STACK_MODEL_REGISTRY_DB`` and
``DIST_STACK_RUNSTORE_DB`` get passed to each domain server.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.client.session import ClientSession

from dist_stack.mcp.client import decode_result, session as connect_session

from .models import ServerSpec

DEFAULT_TIMEOUT_S = 300


class ServerError(RuntimeError):
    """Base error for ServerPool operations."""


class UnknownServerError(ServerError):
    """A requested server is not configured in servers.yaml."""


class ServerConnectError(ServerError):
    """Failed to spawn/initialize a domain server subprocess."""


class ToolCallTimeout(ServerError):
    """A domain tool call exceeded its timeout."""


class _ClientHandle:
    """A live stdio subprocess + session, owned by a dedicated long-lived task.

    ``connect``/``close`` must be called from the same task (the owner task);
    the *session* itself may be used from other tasks.
    """

    def __init__(self, spec: ServerSpec) -> None:
        self.spec = spec
        self._session_cm: Any = None
        self.session: ClientSession | None = None
        self.server_version: str | None = None
        self.error: BaseException | None = None
        self.ready = anyio.Event()
        self._stopped = anyio.Event()
        self._done = anyio.Event()

    async def connect(self) -> None:
        """Enter the connection scopes in the owner task (kept alive).

        Builds ``[command, *args]`` and delegates spawn/init to the shared
        ``dist_stack.mcp.client.session()``; the context manager stays owned by
        this task so it can be torn down in :meth:`close`.
        """
        command = [self.spec.command, *self.spec.args]
        cm = connect_session(
            command,
            env=self.spec.env or None,
            cwd=self.spec.cwd,
            timeout_s=self.spec.timeout_s,
        )
        self._session_cm = cm
        try:
            self.session = await cm.__aenter__()
        except BaseException:
            self._session_cm = None
            raise
        info = self.session.server_info
        self.server_version = getattr(info, "version", None) if info else None

    async def close(self) -> None:
        """Exit the connection scopes (owner task). Idempotent."""
        cm, self._session_cm = self._session_cm, None
        self.session = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def run(self) -> None:
        """The owner-task body: connect, then stay alive until told to close."""
        try:
            await self.connect()
        except BaseException as exc:  # noqa: BLE001 — surfaced via connect()
            self.error = exc
        finally:
            self.ready.set()
        if self.error is not None:
            return
        try:
            await self._stopped.wait()
        except BaseException:
            pass
        await self.close()
        self._done.set()


class ServerPool:
    """Lazy pool of domain-server connections (one subprocess per spec).

    Connections are owned by tasks spawned into the task group bound via
    :meth:`start` (call it from the MCPServer lifespan so the pool's task group
    lives for the whole session).
    """

    def __init__(self, specs: list[ServerSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        self._handles: dict[str, _ClientHandle] = {}
        self._tg: Any = None
        self._closing = False

    def start(self, task_group: Any) -> None:
        """Bind the pool's task group (spawned by the lifespan)."""
        self._tg = task_group

    @property
    def names(self) -> list[str]:
        """Configured server names, in config order."""
        return list(self._specs.keys())

    def spec(self, name: str) -> ServerSpec:
        if name not in self._specs:
            raise UnknownServerError(f"no configured server named {name!r}")
        return self._specs[name]

    async def connect(self, name: str) -> _ClientHandle:
        """Return a live handle, spawning the subprocess on first use."""
        if self._tg is None:
            raise ServerConnectError("ServerPool task group is not bound (start() not called)")
        handle = self._handles.get(name)
        if handle is None:
            handle = _ClientHandle(self.spec(name))
            self._handles[name] = handle
            self._tg.start_soon(handle.run)
            await handle.ready.wait()
        if handle.error is not None:
            raise ServerConnectError(
                f"failed to connect to server {name!r}: {handle.error}"
            ) from handle.error
        return handle

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        """List ``{"name", "description", "required_params"}`` for a server."""
        handle = await self.connect(name)
        assert handle.session is not None
        result = await handle.session.list_tools()
        tools = []
        for tool in result.tools:
            schema = tool.input_schema or {}
            required = schema.get("required", [])
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "required_params": list(required),
                }
            )
        return tools

    async def call_tool(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        """Call ``tool`` on ``name``; wraps the call in ``anyio.fail_after``.

        Returns the JSON-decoded tool result. A ``{"success": False, ...}``
        payload or an ``isError`` result is returned as a plain dict — the
        executor interprets the ``success`` flag. Only connection, timeout and
        transport failures raise.
        """
        handle = await self.connect(name)
        assert handle.session is not None
        spec = self.spec(name)
        timeout = timeout_s or spec.timeout_s or DEFAULT_TIMEOUT_S
        try:
            with anyio.fail_after(timeout):
                result = await handle.session.call_tool(tool, arguments=arguments or {})
        except TimeoutError:
            raise ToolCallTimeout(
                f"tool {tool!r} on server {name!r} timed out after {timeout}s"
            ) from None
        return decode_result(result)

    async def close_all(self) -> None:
        """Tear down every live connection (idempotent).

        Signals each owner task and waits for it to finish closing its scopes,
        so no connection is left half-torn-down when the pool's task group
        exits.
        """
        self._closing = True
        handles = list(self._handles.values())
        for handle in handles:
            handle._stopped.set()
        for handle in handles:
            try:
                with anyio.fail_after(10):
                    await handle._done.wait()
            except TimeoutError:
                pass  # owner task wedged; the task group teardown will reap it
