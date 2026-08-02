# 15. LLM Chat Interface for dist-dashboard (MCP-backed Assistant)

**Status:** Implementation-ready design (oracle-verified against dashboard, runner client, shared mcp client).
**Date:** 2026-08-01

---

# Spec: LLM Chat Interface for dist-dashboard (MCP-backed Assistant)

## A. LLM provider + SDK

**`openai` Python SDK (≥1.30), async client (`AsyncOpenAI`), against an OpenAI-compatible chat-completions endpoint.** Native function-tool calling (`tools`/`tool_calls`), NOT a "call this JSON" convention — per-call `id`s required for `role:"tool"` messages, schema adherence, free error recovery. No agent framework (langchain/autogen — the loop is ~60 lines). Degrades gracefully: endpoints without tool support return text and the loop ends after turn 1.

```python
# apps/dist-dashboard/assistant/llm.py
@dataclass(frozen=True)
class LLMConfig:
    base_url: str; api_key: str; model: str
    timeout_s: float = 120.0; max_tokens: int = 2048

@dataclass(frozen=True)
class LLMToolCall:
    id: str; name: str          # mangled "server__tool"
    arguments: dict[str, Any]

@dataclass(frozen=True)
class LLMResult:
    text: str                   # "" when only tool_calls
    tool_calls: tuple[LLMToolCall, ...]

class LLMClient:                # thin AsyncOpenAI wrapper; no streamlit imports
    async def complete(self, messages, tools, *, temperature=0.2) -> LLMResult: ...

def load_llm_config(secrets: dict | None = None) -> LLMConfig:
    # env LLM_BASE_URL (default https://api.openai.com/v1), LLM_API_KEY, LLM_MODEL (gpt-4o-mini)
    # then st.secrets["llm"], then defaults
```

## B. Server registry (ServerPool from the runner)

**Import `ServerPool` + `load_servers_config` from `dist-workflow-runner` (intra-workspace dep). Do NOT move into `dist_stack.mcp` (CONVENTIONS restricts it; one consumer today).** One backward-compatible change to the runner: `ServerPool.list_tools` gains `"input_schema": tool.input_schema or {}` in the returned dict (`packages/dist-workflow-runner/src/workflow_runner/client.py:174-180`) — the assistant needs full JSON schemas; the runner's own MCP tool picks named fields so its output is unchanged.

**Config**: `apps/dist-dashboard/servers.yaml` — resolution sidebar override > `DIST_DASHBOARD_SERVERS_YAML` env > `<app dir>/servers.yaml`. Entries: the 5 domain servers (gdm, gdm_flow, erad, ditto, shift, same launchers as root servers.yaml) + `workflow_runner` (python -m workflow_runner, WORKFLOW_RUNNER_CONFIG env) + `kg_server` (python -m kg_server), each with the three DIST_STACK_* DB env vars.

**Pool lifecycle in Streamlit** — the MCP stdio client requires enter/exit of cancel scopes in the same long-lived task, but Streamlit reruns the script top-to-bottom per interaction. Solution: `PoolRuntime` — one daemon thread owning an asyncio loop + anyio task group, held in session state:

```python
# apps/dist-dashboard/assistant/pool_runtime.py
class PoolRuntime:
    def start(self): ...   # daemon thread: asyncio.run(bind tg, pool.start(tg), wait shutdown)
    def stop(self): ...    # set shutdown event, join (timeout 5s)
    def call(self, fn, *args, timeout_s=300) -> Any:   # run_coroutine_threadsafe(...).result()
    async def list_tools(self, name) -> list[dict]
    async def call_tool(self, name, tool, args, timeout_s) -> dict
    def statuses(self) -> dict[str, str]               # configured|connected|error
    def check_all(self, per_server_timeout_s=20) -> dict[str, str]
```

Lifecycle: created lazily on first assistant render; torn down when servers.yaml path changes, via "Restart connections" button, or at process exit (daemon thread). The agent loop runs in the script thread; only pool I/O crosses the thread boundary. Session state: `assistant_runtime`, `assistant_servers_yaml`, `assistant_allow_write`, `assistant_server_status`.

## C. Chat + tool-call flow

**Session keys:** `assistant_messages` (list[dict], OpenAI wire format), `assistant_traces` (list[list[TraceRecord]]), `assistant_catalog` (cached tools array), `assistant_catalog_key` (hash of servers.yaml path + allow_write), `assistant_runtime`, `assistant_servers_yaml`, `assistant_allow_write` (default False), `assistant_server_status`. History cap 40 (`DIST_DASHBOARD_MAX_HISTORY`).

**Message shapes (exact):**
```jsonc
{"role": "user", "content": "show me the provenance chain for run wf_abc"}
{"role": "assistant", "content": null, "tool_calls": [
  {"id": "call_01", "type": "function",
   "function": {"name": "kg_server__get_provenance_chain", "arguments": "{\"run_id\": \"wf_abc\"}"}}]}
{"role": "assistant", "content": "The chain for wf_abc is: ..."}
{"role": "tool", "tool_call_id": "call_01", "content": "{\"success\": true, ...}"}
```

**Tool catalog** (`assistant/catalog.py`):
- `mangle(server, tool) = f"{server}__{tool}"`; `demangle(name) -> (server, tool)` (ValueError if no `__` or empty parts; server names must not contain `__`).
- `build_catalog(pool, server_names, *, allow_write)` → for each server `list_tools`, if `policy.catalog_allowed(...)`: `{"type":"function","function":{"name": mangle(...), "description": f"[{server}] {desc}"[:1024], "parameters": tool["input_schema"]}}`. Connect failures → skip server + record status. Catalog cached; rebuilt on (servers.yaml, allow_write) change.

**Agent loop** (`assistant/chat.py`, pure async, no streamlit):
```python
MAX_TURNS = 5; MAX_TOOL_RESULT_CHARS = 8000

async def agent_turn(messages, catalog, router, llm, *, max_turns=5):
    for _ in range(max_turns):
        result = await llm.complete(messages, catalog)
        if not result.tool_calls:
            messages.append({"role":"assistant","content":result.text}); return
        messages.append(assistant_msg_with_tool_calls)
        turn_trace = []
        for call in result.tool_calls:
            record = await router.execute(call)   # never raises
            turn_trace.append(record)
            messages.append({"role":"tool","tool_call_id":call.id,"content":record.to_llm_content(8000)})
        yield assistant_msg, turn_trace           # progressive UI
    # max_turns hit -> UI appends "Stopped after 5 tool rounds" note
```

Non-streaming v1 (spinner with stage text). One failing tool never aborts the turn (error fed to model as tool result). Sequential execution.

**Router** (`assistant/router.py`):
```python
@dataclass
class TraceRecord:
    server; tool; arguments; status  # succeeded|failed|blocked
    error; result; duration_ms
    def to_llm_content(self, limit=8000): ...   # json.dumps(...), truncated with marker
    def to_dict(self): ...

class ToolRouter:
    async def execute(self, call, *, allow_write) -> TraceRecord:
        # demangle -> policy.catalog_allowed (blocked: no pool touch) -> pool.call_tool
        # status=failed iff result.get("success") is False
```

## D. UI design (`assistant/view.py` + app.py "Assistant" page)

- Chat transcript via `st.chat_message`; under each assistant message with tool calls, `st.expander(f"Tool calls · {len(trace)}")` with one row per TraceRecord (server badge + mono tool name + status badge + duration + `st.json` args/result truncated ~2000 chars). New CSS `.trace-row` in styles.py, reusing `.dot`/`.badge`/mono.
- `st.chat_input("Ask about runs, the graph, or run a workflow…")`, disabled while running; spinner with stage captions ("Thinking…", "Calling tool X…").
- Empty state: `styles.empty_state(...)` + 3 suggested prompt buttons (provenance chain, failed runs, graph stats).
- Sidebar: server-status chips (dot green/amber/grey per `runtime.statuses()`), "Test connections" + "Restart connections" buttons, `st.toggle("Enable write tools", value=False)` with `st.warning` banner when on, model caption, "Clear conversation" button.
- Page caption: "Assistant — natural language over the MCP servers. Read-only unless write tools are enabled."

## E. Security

- **Read-only by default + UI toggle.** `assistant/policy.py`: explicit per-server `READ_ONLY_TOOLS` allowlist (curated; drift guarded by tests) + `WRITE_TOOLS` complement + `catalog_allowed(server, tool, *, allow_write)` (allow_write=True → all; else tool in allowlist). Unknown tools default to blocked (allowlist, not denylist). **Two-layer enforcement:** `build_catalog` filters what the LLM sees AND `ToolRouter.execute` re-checks per call (blocked → never touches pool). Write toggle is session state; env `DIST_DASHBOARD_WRITE_TOOLS=1` for headless (documented dangerous).
- **Secrets:** `LLM_API_KEY` from env/`.streamlit/secrets.toml` only (git-ignored), never a UI input. servers.yaml has no secrets.
- **Threat doc:** write-enabled = local admin power (run sims, run_workflow, ingest, export, write stores, spawn 7 subprocesses). Bind Streamlit to `127.0.0.1` (config.toml), no auth in v1, never expose the port. LLM key goes to LLM_BASE_URL — misconfig leaks it. Tool results rendered via st.json/st.code (not markdown) so embedded instructions can't execute as UI; system prompt treats results as data.

## F. File changes

**New (apps/dist-dashboard/):** `assistant/{__init__,llm,catalog,router,chat,policy,pool_runtime,prompt,view}.py`, `servers.yaml`, `.streamlit/secrets.toml.example`, `tests/{fake_llm,fake_pool,fake_tool_server,test_catalog,test_policy,test_router,test_router_integration,test_chat}.py`.

**Modified:** `app.py` (Assistant page + sidebar additions), `pyproject.toml` (deps += dist-workflow-runner workspace, openai>=1.30, anyio; `[tool.uv.sources]`), `styles.py` (.trace-row), `README.md` (assistant section + security posture), `.streamlit/config.toml` (address=127.0.0.1), `packages/dist-workflow-runner/src/workflow_runner/client.py` (list_tools += input_schema).

**Data-layer seam:** `data.py` untouched — the assistant is a pure MCP client (demonstrates the MCP surface end-to-end, incl. runstore queries via `workflow_runner.list_runs`).

## G. Testing (no real LLM, no real servers)

- `fake_llm.py`: `FakeLLM(script)` — pops LLMResult per call, records snapshots, raises on exhaustion.
- `fake_pool.py`: runner's FakePool pattern (~30 lines; copied because runner's conftest isn't importable — no `__init__.py`).
- `fake_tool_server.py`: real MCPServer (echo/add/fail_on_demand) spawned via shared `dist_stack.mcp.client.session()` in one integration test.
- test_catalog: mangle/demangle round-trip, `__` rejection, read-only filtering, input_schema passthrough, connect-failure skip.
- test_policy: catalog_allowed matrix; READ_ONLY_TOOLS ⊆ real tool lists (drift guard); write tools classified.
- test_router: success / `{"success": false}` → failed / ServerError+timeout → failed / **blocked write with allow_write=False → pool never invoked** / malformed name → failed.
- test_router_integration: real fake_server subprocess via session() — echo+add decode.
- test_chat: direct answer; one tool round; two-round chain; **max-turns guard (6 rounds → stopped=True, ≤5 calls)**; failing tool → error fed back + model recovers; blocked write; truncation at 8000; tool_call_id match.

## H. Config + run

Env vars: `LLM_BASE_URL` (default https://api.openai.com/v1), `LLM_API_KEY`, `LLM_MODEL` (gpt-4o-mini), `DIST_DASHBOARD_SERVERS_YAML`, `DIST_DASHBOARD_MAX_TURNS` (5), `DIST_DASHBOARD_MAX_HISTORY` (40), `DIST_DASHBOARD_WRITE_TOOLS` (off). None required — app runs with defaults, "LLM not configured" empty state until key set.

Run: `uv sync` (root) then `uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py`.

`.streamlit/secrets.toml.example`:
```toml
[llm]
base_url = "https://api.openai.com/v1"   # or http://localhost:8000/v1 (vLLM) / :11434/v1 (Ollama)
api_key = "sk-..."                        # never commit
model = "gpt-4o-mini"
```

**Judgment calls:** openai SDK over httpx/anthropic/frameworks; ServerPool reused via workspace dep (one field addition) instead of moved; pool on a dedicated thread (MCP cancel-scope rule vs Streamlit rerun); explicit per-server read allowlist enforced in catalog AND router (name-prefix rules unsound); non-streaming v1; no per-call confirm dialogs in v1; assistant avoids data.py to exercise the MCP surface end-to-end.
