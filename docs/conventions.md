# Ecosystem MCP Server Conventions

> Reproduced faithfully from `src/dist_stack/mcp/CONVENTIONS.md`, the canonical
> pattern for every MCP server in the distribution suite. Design spec:
> `docs/architecture-assessment/10-mcp-sdk-unification-plan.md` §1.
> Reference implementation: `shift/src/shift/mcp_server/`.

`dist_stack.mcp` is a **conventions home only** — no registry MCP server lives
here (YAGNI). The functional API is `dist_stack.registry` /
`dist_stack.manifest`. Ecosystem servers copy this exact shape under their own
`<pkg>/mcp/` (or `mcp_server/`) package.

## Package layout

```
<pkg>/mcp/
├── server.py          # create_server(): MCPServer + register() calls (<= ~80 lines)
├── __init__.py        # version + re-exports
├── __main__.py        # optional thin main → create_server().run(transport="stdio")
├── common.py          # shared helpers (path/model_ref resolution, serializers)
├── tools/<domain>.py  # each: def register(mcp) -> None: @mcp.tool() ...
├── resources/<domain>.py  # each: def register(mcp): @mcp.resource(...) ...
└── prompts/workflows.py   # def register(mcp): @mcp.prompt() ...
```

## Module contract

Every module (tools/resources/prompts) exports a single `register(mcp)`:

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def add_node(ctx: Context[AppContext], graph_id: str, node_name: str,
                 longitude: float, latitude: float,
                 assets: list[str] | None = None) -> str:
        """<description>

        Args:
            graph_id: ...
        Returns:
            JSON confirmation...
        """
        ...
```

`server.py` composes them:

```python
def create_server() -> MCPServer:
    mcp = MCPServer("name", instructions=..., lifespan=app_lifespan)
    from pkg.mcp.tools import nodes, edges
    nodes.register(mcp)
    edges.register(mcp)
    ...
    return mcp
```

## Rules

- **Schema from annotations + docstrings.** `inputSchema` `properties` from
  typed params; `default` from Python defaults; `required` = params without
  defaults; `enum` from `Literal[...]`; optional/union from `X | None = None`
  (plus a runtime XOR check via a `_get_system_path_arg`-style helper for
  system_path/model_ref pairs).
- **`ctx: Context[AppContext]` first** only if the tool needs session state
  (lifespan context). Stateless-per-call tools omit it.
- **Return values:** `str` (JSON) — the JSON-string convention. Errors return
  `{"success": False, "error": ...}` payloads, never raise.
- **Resources:** static `@mcp.resource("scheme://static")` take **no** params
  (mcp 2.0 raises otherwise); templates `@mcp.resource("scheme://{param}")`
  take exactly the templated params. `name=`/`mime_type=` kwargs map to the
  old `Resource(...)` fields.
- **Prompts:** `@mcp.prompt()` returning `str`; prompt args become params.
- **Naming:** verb-first snake_case, no prefix (`add_node`,
  `export_system_json`, not `node_add` / `shift_add_node`). Resource URI
  schemes stay per-repo (`shift://`, `gdm://`, ...).
- **Control flags** (if needed): keep `_TOOL_CALLS_ENABLED` in
  `tools/control.py` and wrap non-control tools at registration with one
  `@_guard` decorator.

## Shared helpers

`dist_stack.mcp.serialization` provides `json_safe(obj)` (JSON-dump with
coercion of non-JSON values) and `error_payload(message)` (the
`{"success": False, "error": ...}` string). Import them directly —
dist-stack adds no `mcp` dependency.

```python
from dist_stack.mcp import json_safe, error_payload

json_safe({"run_id": "sim_000000000001", "when": datetime.now()})
# '{"run_id": "sim_000000000001", "when": "2026-08-01T12:00:00+00:00"}'

error_payload("no run found for run_id=sim_000000000001")
# '{"success": false, "error": "no run found for run_id=sim_000000000001"}'
```

```{eval-rst}
.. function:: json_safe(obj, **kwargs) -> str

   JSON-encode ``obj``, coercing non-serializable values instead of raising:
   datetimes/date/time → ISO-8601 strings, sets → lists, enums → ``value``,
   paths/UUIDs → ``str()``, dataclasses → dicts, anything else → ``str()``.

.. function:: error_payload(message, **extra) -> str

   Standard error payload string ``{"success": False, "error": ...}``. Tools
   return this instead of raising, per the CONVENTIONS.md contract.
```
