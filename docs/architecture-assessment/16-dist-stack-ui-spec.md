# Spec 16 — Professional dist-stack UI (new repo)

Status: **Draft for review (rev 2)** · Date: 2026-08-02 · Supersedes: none (new)
Builds on: spec 09 (registry), 11 (runstore + workflow runner), 12 (KG),
13 (monorepo), 15 (dashboard assistant). Requires: spec 17 (engine event
hook + cancellation).

Revision 2 notes: reconciled with oracle architecture review — corrected the
server topology (5 spawned + 2 in-process, not "7 stdio"), added the engine
event-hook/cancel dependency (spec 17), localhost CSRF guards, keyring
degradation, canvas-topology semantics, and **Path C: conversational
LLM-driven workflow assembly** (the flagship capability).

## 1. Goal

Build a professional-grade, production-quality web UI for the dist-stack
ecosystem in a **new repository** (`dist-stack-ui`), replacing
`apps/dist-dashboard` (Streamlit, read-only) as the human-facing product.

The UI must:

1. Let users **create, edit, validate, run and monitor simulation workflows**
   over the 5 domain MCP servers (gdm, gdm-flow, erad, ditto, shift) via
   `workflow_runner`.
2. Let users **assemble simulation workflows conversationally** — describing
   the pipeline in natural language to the LLM assistant, which selects
   tools, chains them (threading intermediate results), executes, and can
   promote the successful chain into a durable reusable workflow.
3. Let users **browse** the recorded state of the three stores (runstore,
   knowledge graph, model registry) with a provenance tree and a real graph
   visualization.
4. Provide an **LLM assistant** with **first-class support for OpenAI,
   Fireworks AI, DeepSeek (API keys) and Ollama (local models)**.
5. Stream **live run progress** (per-step status, tool calls, token deltas)
   to the browser.
6. Be local-first and deployable later, with a sane security posture.

## 2. Repo disposition

- **New repo**: `dist-stack-ui` (owner `AadilLatif`), **separate from the
  dist-stack monorepo** — it is a consumer/product over the ecosystem, not a
  member of it.
- **Relationship to the monorepo**: depends on `dist-stack-model-registry`,
  `dist-workflow-runner` and `dist-kg` as path dependencies
  (`[tool.uv.sources] … = { path = "../dist-stack/packages/…" }`) so a single
  editable install gives the backend the whole engine. The only monorepo
  changes are the reviewed engine contributions in **spec 17** (event hook +
  cancellation) — that is platform code, not UI code.
- **`apps/dist-dashboard` disposition**: kept as-is for now (cheap read-only
  viewer / quickstart). Marked deprecated once dist-stack-ui reaches
  functional parity on the read side. No migration of Streamlit code.

## 3. Architecture

**One engine, two doors.** The FastAPI backend is the *engine*: it imports
`dist_stack.*`, `workflow_runner` and `kg_server` **in-process**, owns the
`ServerPool` (which spawns the **5 domain stdio servers** from `servers.yaml`;
`workflow_runner` and `kg_server` are imported as libraries, not spawned),
and exposes:

- **REST + SSE to the SPA** (browser-facing; JSON over HTTP, SSE for streams)
- **Optionally later, an MCP server surface** for LLM agents (deferred —
  see §11).

Rationale: the browser cannot spawn stdio MCP subprocesses, so the UI never
talks MCP directly. The TS MCP SDK (`@modelcontextprotocol/sdk` 1.30 /
`@modelcontextprotocol/client` 2.0) is only relevant if/when the backend
exposes an MCP surface or a hosted multi-tenant offering exists.

### 3.1 Server topology (corrected)

| Server | Spawned stdio? | Surface for the UI |
|---|---|---|
| gdm, gdm-flow, erad, ditto, shift | **yes** (ServerPool from servers.yaml) | dynamic `list_tools` |
| workflow_runner | **no — in-process library** | static synthesized tool entries (its 8 tools) |
| kg_server | **no — in-process library** (tools are thin wrappers over `dist_stack.kg`) | static synthesized tool entries (its 8 tools) |

Consequence: the **Tool Catalog and the assistant catalog cannot use
`pool.list_tools` for all servers** — dynamic listing for the 5 spawned,
plus static curated entries for the 2 engine servers (mirroring how
`assistant/policy.py KNOWN_TOOLS` already works). Plan this in Phase 1, not
Phase 2.

```
┌─────────────────────────────┐
│  Browser (React SPA)        │
│  Vite · TS · @xyflow/react  │
│  TanStack Query/Router      │
│  Vercel AI SDK (chat)       │
└──────────────┬──────────────┘
               │ REST + SSE (127.0.0.1)
┌──────────────▼──────────────┐
│  FastAPI backend (engine)   │
│  imports dist_stack/workflow_runner in-process
│  ServerPool → 5 stdio domain servers (servers.yaml)
│  + workflow_runner & kg_server as libraries
│  ProviderRegistry → OpenAI/Fireworks/DeepSeek/Ollama
│  SSE broker per run / per chat turn
└──────────────┬──────────────┘
               │ optional MCP surface for agents (deferred)
┌──────────────▼──────────────┐
│  dist-stack monorepo        │
│  runstore.db · kg.db · registry.db (~/.cache/dist-stack)
│  5 domain servers: gdm · gdm-flow · erad · ditto · shift
└─────────────────────────────┘
```

### 3.2 Frontend stack (verified against npm, 2026-08)

| Concern | Choice | Version | Why |
|---|---|---|---|
| Build | Vite + React + TS | current | Component-driven; no SSR/SEO needs |
| Workflow canvas | `@xyflow/react` (React Flow) | 12.11.2, MIT | Industry default (n8n, Airflow use it); custom nodes are plain components |
| Auto-layout | `dagre` (or `elkjs`) | current | DAG layout for graphs |
| Data fetching/cache | TanStack Query | current | Run polling, invalidation |
| Routing | TanStack Router | current | File-less typed routes |
| Tables | TanStack Table | current | Runs/registry tables |
| Forms | `react-hook-form` + `zod` | current | Input-schema-driven step forms |
| Chat | Vercel AI SDK `ai` + `@ai-sdk/react` | 7.x | `useChat`, works in plain SPA |
| Charts | Recharts | current | Results viz |
| UI kit | Tailwind + shadcn/ui | current | Prefect UI v2 pattern; fast, tasteful |
| Icons | lucide-react | current | |

This mirrors Prefect UI v2's reboot (Vite + React + TanStack + shadcn +
Recharts + Stepper for wizards) — the strongest external validation.

### 3.3 Backend stack

| Concern | Choice | Why |
|---|---|---|
| API | FastAPI (>=0.135) | First-class SSE (`fastapi.sse`) |
| ORM/schema | Pydantic v2 | Strict request/response contracts |
| Process mgmt | `anyio` task groups | `execute_workflow` and `ServerPool.call_tool` are already async; the pool's task-group ownership rule is handled in FastAPI's lifespan, replicating `server.py`'s pattern |
| Config | `servers.yaml` (existing format) + Pydantic settings | Zero-config parity with the runner |
| Streaming | `EventSourceResponse` / `ServerSentEvent` | Run + token streaming, with heartbeat comments and `Last-Event-ID` resume |
| Keys | env vars → Python `keyring` (25.x) | Local-first secret storage with headless degradation |

### 3.4 Key-handling model (LLM providers)

Keys never leave the backend and never touch `localStorage`:

1. **Env vars win**: `OPENAI_API_KEY`, `FIREWORKS_API_KEY`,
   `DEEPSEEK_API_KEY` (or the generic `LLM_*` trio for a single default).
2. **Settings UI** stores only **non-secret** provider config (base_url,
   enabled flag, default model per provider) in a local config table; the
   actual keys go to the OS keychain via `keyring` (or stay in env).
3. **Headless keyring degradation**: on systems without a Secret Service
   daemon, `keyring` raises — the Settings UI must detect this, show
   "keyring unavailable — use env vars", and never crash the page.
4. Never log, never `git commit`, never send to the browser.

## 4. LLM provider layer

All four providers are **OpenAI-compatible** chat-completions + tool-calling,
so one `openai` Python SDK client (parameterized by `base_url`/`api_key`) is
the single client — no per-provider SDKs.

| Provider | base_url (official) | Tool calling | Notes |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | yes | baseline |
| Fireworks | `https://api.fireworks.ai/inference/v1` | yes | usage only in last streamed chunk |
| DeepSeek | `https://api.deepseek.com` (also accepts `/v1`) | yes | **thinking on by default** → per-provider `extra_body={"thinking":{"type":"disabled"}}` to opt out (never sent to other providers) |
| Ollama | `http://localhost:11434/v1` | yes (no `tool_choice`) | `api_key='ollama'` required-but-ignored; streaming + JSON mode + tools supported |

### 4.1 `ProviderRegistry` (backend)

```python
@dataclass
class Provider:
    key: str            # "openai" | "fireworks" | "deepseek" | "ollama"
    label: str
    base_url: str
    api_key_env: str
    default_model: str
    quirks: ProviderQuirks   # thinking opt-out, tool_choice, usage semantics, max_tokens

class ProviderRegistry:
    PROVIDERS: dict[str, Provider]
    def client(self, key, api_key=None) -> AsyncOpenAI
    def resolve(self, request) -> tuple[AsyncOpenAI, Provider, model]
```

- **No fallback Router in v1** (oracle P2-7): for a single-operator local
  tool, manual provider switching in the UI is the right failure mode.
  Revisit with LiteLLM only if multi-user budget/usage tracking arrives.
- **Provider quirks handled centrally**: DeepSeek thinking opt-out
  (per-provider `extra_body` only), Ollama missing `tool_choice` /
  `api_key='ollama'`, Fireworks usage-in-final-chunk, per-provider
  `max_tokens` and timeout semantics.
- **Ollama health**: probe `GET {OLLAMA_HOST}/api/tags` for installed model
  names; the settings UI lists them as pickable models.

## 5. Views (page inventory)

Top-level navigation (sidebar):

1. **Dashboard** — store health (runstore/KG/registry), run status cards,
   recent runs, KG node-type cards, registry latest — evolved from
   dist-dashboard.
2. **Workflows** — the product centerpiece (see §6).
3. **Runs** — filterable/paginated run history; run detail page with live
   timeline, step table, artifacts, payload, provenance jump; **re-run /
   compare runs** (diff step results). Stale-`running` rows (backend crash)
   show a stale indicator.
4. **Provenance** — search a node (run/artifact/model) → React Flow tree
   (up/down, depth), neighbors table. Uses `query_provenance` /
   `get_provenance_chain` / `get_neighbors` from kg_server.
5. **Knowledge Graph** — real graph visualization (React Flow), node-type
   filters, node click → provenance. `graph_stats`, `search_nodes`.
6. **Registry** — model versions table, detail, metadata, `model_ref`
   resolution.
7. **Tool Catalog** — browse every server's tools with full input schemas:
   dynamic `list_servers` + `list_tools` for the 5 spawned servers, plus
   static synthesized entries for `workflow_runner`/`kg_server` (§3.1).
8. **Assistant** — chat over the MCP pool with tool-call trace expanders,
   live token streaming, provider picker (OpenAI/Fireworks/DeepSeek/Ollama),
   and **conversational workflow assembly** (§6.5).
9. **Settings** — LLM provider config (keys via keyring, models, enabled
   flags), servers.yaml path/status, server health page (replaces the
   sidebar chips). Surfaces machine-specific config such as gdm-flow's `cwd`.

## 6. Workflow creation — the thorough part

Users create simulation workflows **three** deliberate ways, mirroring how
Prefect/n8n/Airflow converge: **guided wizard for 80% of users, visual
canvas for the experts, and conversational LLM assembly as the flagship.**
All three produce the same `schema_version: 1` workflow JSON that
`workflow_runner` already validates and executes.

### 6.1 The workflow object (what we build toward)

Existing schema (`templates.py`, `WorkflowSpec`), unchanged:

```json
{
  "schema_version": 1,
  "workflow_id": "run_ac_pf_workflow",
  "version": "1.0.0",
  "name": "Run AC power flow",
  "description": "Load a GDM system and run an AC power-flow study.",
  "source_prompt": "gdm-flow://run_ac_pf@1",
  "inputs": [{"name": "system_path", "type": "string", "required": true}],
  "steps": [
    {
      "id": "step_1",
      "server": "gdm_flow",
      "tool": "run_ac_pf",
      "args": {"system_path": "${system_path}", "solver": "ac_pf", "include_details": true},
      "capture": "pf_result",
      "on_failure": "fail"
    }
  ],
  "outputs": [{"name": "pf_summary", "from": "pf_result"}]
}
```

Step args support `${input}` and `${capture.key.subkey}` substitution;
`capture` stores a step result in the run env for later steps/outputs;
`on_failure` is `fail` (skip the rest) or `continue`. The runner executes
steps **strictly sequentially, in list order** — no parallelism, no
branching (this constrains the canvas, §6.3).

### 6.2 Path A — Guided wizard (default, recommended for new users)

A 5-step Stepper (react-hook-form + zod, client-side validation at each
step, JSON preview at the end):

1. **Details** — name, `workflow_id` (slug, auto-suggested from name),
   version, description. Validate against `^[A-Za-z0-9][A-Za-z0-9_.-]*$`.
2. **Inputs** — dynamic list of inputs: name, type (string/number/boolean/
   path/model_ref), required flag, description. Model refs get a picker into
   the registry.
3. **Steps** — one row per step, **reorderable (up/down)**. For each step
   the user picks:
   - **Server** (dropdown populated from the catalog — live status dots),
   - **Tool** (dropdown populated from `list_tools(server)` once the server
     is picked — grouped by server, searchable),
   - **Arguments** — a form **auto-generated from the tool's `input_schema`**
     (JSON Schema → zod): required vs optional, types, enums, defaults.
     String fields can be toggled to **"use a variable"** → a
     `${input}` / `${capture.*}` picker (only valid names offered).
   - **Capture** (optional) — name for the result.
   - **on_failure** — fail / continue (toggle with tooltip explaining skip).
   - Duplicate step-id check client-side (server catches it anyway).
4. **Outputs** — map output names to `from:` a captured var or input.
5. **Review & save** — read-only JSON preview (syntax-highlighted), then
   `create_workflow`. **Version-bump guard**: if the workflow already has
   runs, warn and suggest a version bump before saving (the runstore records
   `workflow_version` per run).

Also: **Start from template** — pick a packaged workflow
(`run_ac_pf_workflow`, `feasibility_study`) or any saved one, load it into
the wizard pre-filled, tweak, save-as.

### 6.3 Path B — Visual canvas (for experts)

A React Flow editor:

- **Left palette**: servers grouped with live status; expanding a server
  shows its tools (fetched from the catalog). Drag a tool onto the canvas
  → becomes a **step node**.
- **Node** = a step: server badge, tool name, capture tag, on_failure icon,
  status ring (once run). Click → side panel with the auto-generated
  argument form (same form engine as the wizard), variable picker, and
  capture/failure controls.
- **Edges = execution order**, matching the runner's **strictly sequential**
  semantics. Because the executor has no parallelism/branching, the canvas
  must either (a) constrain the graph to a single chain, or (b) accept any
  DAG and **topologically sort on save**, with a loud "branches execute
  sequentially" note. **Unconnected steps keep insertion order.**
  `source` → `target` means target runs after source; a step may capture a
  value later steps reference as `${capture.*}`. Cycle detection on connect
  (`isValidConnection`).
- **Topology actions**: reorder by dragging edges, delete nodes, duplicate
  a node, auto-layout (dagre). Node palette search.
- **Live validation bar**: missing required args, unknown server/tool,
  dangling variable refs, empty workflow, duplicate step ids. Blocked
  "Save" until valid.
- **Save** → `create_workflow`; **Run** → straight into §6.6.

**Wizard → canvas upgrade**: a workflow created in the wizard opens in the
canvas for topology editing; a canvas workflow opens in the wizard for
guided editing. One canonical JSON, two editors.

### 6.4 Path C — Conversational LLM-driven assembly (flagship)

The user describes the pipeline in natural language; the assistant plans,
calls tools across the 5 domain servers in sequence, threads intermediate
results, and reports. The executed chain can be **promoted** into a durable
reusable workflow template.

**The canonical example (from the product brief):**
> "Select a GDM model, run resilience analysis using erad, get an updated
> post-contingency GDM model, run powerflow on that GDM model."

The LLM's plan (assembled dynamically from the live catalog):

| # | Server | Tool | Key args | Threaded state |
|---|---|---|---|---|
| 1 | gdm | `get_system_summary` | `system_path` (user-supplied) | validates/selects the model |
| 2 | erad | `load_distribution_model` | `source` = the GDM path | → `asset_system_id` |
| 3 | erad | `load_hazard_model` (or `create_forefire_hazard`) | user choice / default | → `hazard_system_id` |
| 4 | erad | `run_simulation` | `asset_system_id`, `hazard_system_id`, `curve_set` | resilience analysis |
| 5 | erad | `get_failed_assets` / `export_to_json` | simulation id | post-contingency model path |
| 6 | gdm_flow | `run_ac_pf` | `system_path` = the **post-contingency** model | final power-flow results |

Each step's captured result feeds the next step's args via the same
`${capture}` threading the workflow engine already supports — but here the
LLM chooses the chain at runtime instead of a fixed template.

**Mechanics that make this work:**

1. **Tool-call chaining with state threading.** The assistant's
   `ToolRouter` executes calls through the pool; intermediate values
   (`asset_system_id`, exported model path) are captured and offered to the
   LLM for subsequent calls. The trace (`TraceRecord`) already records
   server/tool/args/result per call — it is the chain's audit log.
2. **Write-tool gating.** Conversational assembly is a *write* activity
   (`run_simulation`, `export_to_json`, `run_ac_pf`, …). It is gated behind
   the explicit "Enable write tools" toggle (policy model from spec 15):
   read-only chat works out of the box; assembly requires the toggle.
   Two-layer enforcement stays (catalog filter + router re-check).
3. **Chain-to-workflow promotion.** When a conversational chain succeeds,
   the UI offers **"Save as workflow"**:
   - *Deterministic (default, auditable)*: steps = the executed tool calls
     (server, tool, args) with user-supplied root values lifted to `inputs`
     and threaded values expressed as `${capture.*}`; `capture` names
     generated from the step id; `outputs` from the final results;
     `source_prompt = "assistant://<turn-id>"` for provenance.
   - *LLM-assisted (optional)*: the model generalizes the chain into a
     parameterized template (cleaner names, merged args). Off by default;
     shown diffed against the deterministic version.
   - Either way `create_workflow` validates it, then the user can open it in
     the wizard or canvas and re-run it — chat becomes a template factory.
4. **Live chain visualization in the chat.** Each conversational assembly
   renders as a mini step-list (the §5.7 catalog icons + statuses) with
   threaded values shown as chips, so "how did we get from the GDM model to
   this power-flow result" is never a black box.
5. **Safety rails.** Every tool call is shown before execution (args +
   target server) with a confirm on first write-tool call of a session; the
   user can stop the chain between steps; results render as data, never as
   markdown/HTML.

**Why this is the flagship:** it collapses "build workflow" and "run
simulation" into a single dialogue. Users who can't or won't learn the tool
catalog can still assemble correct multi-server pipelines; the durable
template is a by-product of a successful conversation, and `source_prompt`
keeps the provenance back to the prompt that created it.

### 6.5 How a simulation actually runs (UX flow)

```
Run a workflow
  ├─ Pick workflow (list from list_workflows; card shows step_count, source_prompt)
  ├─ Inputs form (auto-generated from workflow.inputs; model_ref picker)
  ├─ Optional: reuse_run_id → prior-graph hint shown in the panel
  │    (note: reuse_run_id only accepts a prior SUCCEEDED run of the same
  │     workflow_id — the UI disables it otherwise)
  └─ "Run" → POST /workflows/{id}/runs
       → run_id returned immediately
       → Run detail page opens with a LIVE timeline (spec 17 event hook):
           per step: pending → running (tool, args_resolved) → succeeded/failed/skipped
           each step's result JSON expandable
           artifacts list populates as they attach
       → "Cancel" button (REST) sets the engine abort flag → runstore row
         becomes cancelled, remaining steps marked skipped
       → on finish: status banner, outputs panel, "Open provenance" jump,
         "Re-run" (pre-fills inputs), "Compare with run X"
```

Streaming (§8) makes the timeline live; polling the runstore is the
fallback for clients that reconnect (SSE replay via `Last-Event-ID`).

## 7. Backend API surface (REST, v1)

### Workflows
- `GET /workflows` — list (id, version, name, step_count, source_prompt)
- `GET /workflows/{id}` — full template
- `POST /workflows` — create (validate, `overwrite` flag)
- `PUT /workflows/{id}` — update (UI surfaces the version-bump guard)
- `DELETE /workflows/{id}`
- `POST /workflows/{id}/validate` — client-side validation server-side too

### Tools / catalog
- `GET /servers` — live status from the pool (`list_servers`)
- `GET /servers/{name}/tools` — full tool list with input schemas
  (`list_tools` for the 5 spawned; static for the 2 in-process)
- `GET /catalog` — the merged catalog (spawned + static) the assistant uses

### Runs
- `POST /workflows/{id}/runs` — start run (`run_workflow`; accepts inputs,
  run_id, reuse_run_id)
- `GET /runs` — list (filters: status, workflow_id, tool, run_type, limit)
- `GET /runs/{run_id}` — detail + artifacts (`get_run`)
- `GET /runs/{run_id}/events` — SSE stream (§8)
- `POST /runs/{run_id}/cancel` — best-effort cancel (REST; engine abort
  flag, spec 17)

### Stores
- `GET /kg/stats`, `GET /kg/nodes`, `GET /kg/provenance`, `GET /kg/chain`,
  `GET /kg/search` — mirror kg_server query tools
- `GET /registry/models` — registry listing

### Assistant (incl. conversational assembly)
- `POST /assistant/turns` — SSE-streamed chat turn (messages, provider,
  model, allow_write) → token deltas + tool_call events
- `GET /assistant/catalog` — tool catalog per allow_write
- `POST /assistant/promote` — turn trace + intent → workflow JSON
  (deterministic; optional LLM-assisted variant) → `create_workflow`

### Settings
- `GET/PUT /settings/providers` — non-secret provider config
- `PUT /settings/providers/{key}/key` — store key via keyring
- `GET /settings/providers/{key}/models` — Ollama: probe `/api/tags`

## 8. Real-time streaming (SSE)

**FastAPI >= 0.135 first-class SSE** (`from fastapi.sse import
EventSourceResponse, ServerSentEvent`).

- **Run progress**: `GET /runs/{run_id}/events` →
  `event: running|succeeded|failed|skipped|cancelled`, `data: {step_id,
  tool, server, status, args_resolved, error, duration_ms, artifact_id}`.
  **This requires the engine event hook (spec 17)** — the current
  `execute_workflow` writes to the runstore only at start and end. The hook
  (`on_step: Callable[[StepResult], None]`, ~10 lines upstream, unit-tested)
  gives exact parity including substitution failures.
- **Assistant tokens**: `POST /assistant/turns` streams `event: token`
  (delta) and `event: tool_call`; `[DONE]` sentinel. Same shape works across
  all four providers via the openai SDK `stream=True`.
- Browser-native `EventSource` is GET-only → run progress uses GET+SSE; the
  chat turn uses POST via `fetch` + `ReadableStream` reader (Vercel AI SDK
  handles this).
- **Heartbeats**: periodic SSE comment lines keep idle connections alive
  through proxies/browsers.
- **Reconnect**: on reconnect, replay buffered events since `Last-Event-ID`,
  then terminate; polling the runstore is the stated fallback.

## 9. Security posture

1. **v1: localhost-only, no auth.** Backend binds `127.0.0.1:8000`; Vite dev
   `127.0.0.1:5173` with CORS restricted to that origin.
2. **Localhost CSRF / DNS-rebinding guards** (Phase 1, not v2): reject
   requests whose `Origin` header (when present) isn't
   `http://127.0.0.1:5173`, and validate the `Host` header (defeats DNS
   rebinding against a bound 127.0.0.1). CORS alone is insufficient — a
   malicious website can POST to `127.0.0.1` without preflight. The optional
   API-key gate does not help the browser (the SPA can't hold the key), so
   Origin/Host checks are the actual browser-facing control.
3. **Optional static API-key gate** (single `Authorization: Bearer` via a
   FastAPI dependency) for LAN exposure. No user management in v1.
4. **Browser hygiene from day one**: no keys/tokens in `localStorage`
   (XSS-exfiltratable); httpOnly cookies if sessions are ever added; strict
   CSP on the SPA; sanitize anything rendered from tool results (tool output
   is data, never markdown/HTML from the model).
5. **Read-only by default** for the assistant: reuse the policy allowlist
   model from `assistant/policy.py` (two-layer: catalog filter + router
   re-check). Write tools — including conversational assembly — behind the
   explicit "Enable write tools" toggle, with first-write confirmation.
6. **When hosted (v2)**: reverse proxy + OIDC, secrets manager, per-user
   scoping, MCP OAuth 2.1 + PKCE if the MCP surface goes remote.

## 10. Implementation phases

- **Phase 0 — Engine contributions (spec 17)**: `on_step` event hook +
  cancellation (abort flag → runstore `cancelled` + remaining steps
  `skipped`) in the monorepo, unit-tested. Reviewed and merged upstream.
- **Phase 1 — Engine**: FastAPI skeleton in the new repo, ServerPool wiring
  (lifespan replicating `server.py`), merged catalog (5 dynamic + 2 static),
  REST endpoints for servers/tools/workflows/runs, SSE broker driven by the
  spec-17 hook, cancel endpoint, **Origin/Host guards**, keyring handling
  with headless degradation. Contract tests against monorepo HEAD (mandatory
  CI job).
- **Phase 2 — Read UI**: Dashboard, Runs (+detail, live timeline), Registry,
  Tool Catalog, server health. React Flow provenance tree.
- **Phase 3 — Authoring**: Wizard (Path A), then Canvas (Path B) with
  chain/toposort semantics, validation, save/run wiring, version-bump guard.
- **Phase 4 — Assistant & conversational assembly**: provider registry,
  chat with token/tool_call streaming, policy gate, provider picker, Ollama
  model list, **chain threading + "Save as workflow" promotion (Path C)**.
- **Phase 5 — Polish & deploy**: compare runs, graph viz for the KG,
  packaging (single-command launch: `uv run dist-stack-ui serve`), docs,
  functional parity gate to retire dist-dashboard.

## 11. Open decisions (resolved at review)

1. **LiteLLM / Router**: not in v1. Thin `ProviderRegistry` + manual
   provider switching in the UI; revisit LiteLLM only for multi-user
   budgets/usage (agreed with oracle).
2. **MCP surface for the backend**: **deferred past Phase 5.** It duplicates
   the assistant's job and adds a second protocol to maintain; the REST+SSE
   API is the product. Revisit only when agent-driven UI is a real
   requirement.
3. **Realtime**: SSE (resolved — auto-reconnect, FastAPI first-class,
   heartbeat + `Last-Event-ID` replay; cancel is a REST call).
4. **Graph rendering**: React Flow for both workflow canvas and KG
   provenance (one skill). Pixi.js (Prefect-style) only if scale demands.
5. **Key storage**: `keyring` with env precedence, plus headless
   degradation path (§3.4).
