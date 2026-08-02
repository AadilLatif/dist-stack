"""dist-workflow-runner MCP server — main application wiring.

``create_server()`` builds the :class:`MCPServer` and registers the tool,
resource and prompt modules; the lifespan initialises the
:class:`~workflow_runner.client.ServerPool` from the resolved config and tears
it down (cancellation-shielded) on exit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from mcp.server import MCPServer

from workflow_runner import __version__
from workflow_runner.client import ServerPool
from workflow_runner.config import load_servers_config
from workflow_runner.models import AppContext

INSTRUCTIONS = (
    "dist-workflow-runner orchestrates versioned JSON workflows across the "
    "distribution-suite domain MCP servers (gdm, gdm_flow, erad, ditto, shift). "
    "Use list_servers/list_tools to discover domain tools, "
    "list_workflows/get_workflow to inspect templates, and run_workflow to "
    "execute one (every run is recorded in the shared runstore with an "
    "execution-graph artifact)."
)


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    """Initialise the server pool + config; tear the pool down on exit.

    The pool owns a task group for its connection-owner tasks, so every
    subprocess connection's anyio scopes are entered/exited in a long-lived
    task (never inside a per-request handler task).
    """
    config = load_servers_config()
    pool = ServerPool(config.servers)

    async with anyio.create_task_group() as tg:
        pool.start(tg)
        ctx = AppContext(config=config, pool=pool)

        # Static resources have no Context param — expose the lifespan state
        # module-locally (same pattern as shift's docs resources).
        from workflow_runner.resources import index

        index.set_app_context(ctx)

        try:
            yield ctx
        finally:
            # ServerPool teardown: shield the shutdown from client cancellation
            # so subprocesses are always reaped.
            with anyio.CancelScope(shield=True):
                await pool.close_all()


def create_server() -> MCPServer:
    """Create and configure the MCPServer instance."""
    mcp = MCPServer(
        "dist-workflow-runner",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=app_lifespan,
    )

    # -- Register tool modules -------------------------------------------------
    from workflow_runner.tools import runs, servers, workflows

    servers.register(mcp)
    workflows.register(mcp)
    runs.register(mcp)

    # -- Register resources ----------------------------------------------------
    from workflow_runner.resources import index

    index.register(mcp)

    # -- Register prompts ------------------------------------------------------
    from workflow_runner.prompts import workflows as workflow_prompts

    workflow_prompts.register(mcp)

    return mcp
