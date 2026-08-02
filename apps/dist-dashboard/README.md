# dist-dashboard

A read-only visibility browser for the dist-stack ecosystem. It lets a human
browse the recorded state of the three stores — run results, the knowledge
graph, and the model registry — without touching any repo code or writing
anything to the databases.

Built with [Streamlit](https://streamlit.io). All data is read through the
dist-stack Python APIs (`dist_stack.runstore`, `dist_stack.kg`,
`dist_stack.registry`).

## Run it

```bash
# from the dist-stack monorepo root (uv workspace)
uv sync
uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py
```

Open the printed URL (default `http://localhost:8501`).

## The three stores

The app reads from three SQLite databases. Each one falls back in this order:

1. the path typed in the sidebar Settings panel, then
2. the matching environment variable, then
3. `~/.cache/dist-stack/<default filename>`.

| Store        | Env var                        | Default file           |
| ------------ | ------------------------------ | ---------------------- |
| Runstore     | `DIST_STACK_RUNSTORE_DB`       | `runstore.db`          |
| Knowledge graph | `DIST_STACK_KG_DB`           | `kg.db`                |
| Model registry | `DIST_STACK_MODEL_REGISTRY_DB` | `model_registry.db`    |

If a database file does not exist, the app shows an empty state instead of
crashing — it never creates the file itself.

## Views

- **Overview** — counts per store, recent runs, most-connected graph nodes.
- **Run History** — the runstore table with tool / run type / status / session
  filters and pagination; selecting a run shows its detail (message, payload,
  model fields, attached artifacts).
- **Provenance** — pick a node (by id, or by searching a run id / artifact
  path / model id) and trace its ancestry or descendants as an indented tree,
  plus a neighbor table.
- **Knowledge Graph** — node/edge counts and a node search by type and label.
- **Registry** — registered model versions with stored paths and hashes.
- **Assistant** — a chat interface over the MCP servers. Ask questions about
  runs, the graph or the ecosystem in plain language; the assistant calls
  domain-server tools through the same `ServerPool` the workflow runner uses.
  Read-only unless write tools are enabled in the sidebar.

## Assistant (LLM chat)

A pure-MCP chat assistant: the model gets a tool catalog built from
`servers.yaml` (spec 15). No agent framework, no `data.py` — it talks to the
ecosystem over the same MCP surface the runner uses.

### Run it

```bash
# env for the LLM endpoint (or use .streamlit/secrets.toml — see below)
export LLM_API_KEY=sk-...
# optional:
# export LLM_BASE_URL=http://localhost:8000/v1   # vLLM / Ollama :11434/v1
# export LLM_MODEL=gpt-4o-mini

uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py
```

Then open the **Assistant** page. Copy
`.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` to keep the key
out of your shell (env vars win over secrets).

Env vars:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | — | the API key (also via `[llm]` secrets block) |
| `LLM_MODEL` | `gpt-4o-mini` | model name |
| `DIST_DASHBOARD_SERVERS_YAML` | `<app dir>/servers.yaml` | MCP server config |
| `DIST_DASHBOARD_MAX_TURNS` | `5` | max tool rounds per turn |
| `DIST_DASHBOARD_MAX_HISTORY` | `40` | messages kept in context |
| `DIST_DASHBOARD_WRITE_TOOLS` | off | headless write-tools override (dangerous) |

### Security posture

- **Read-only by default.** Tools are gated by an explicit per-server
  allowlist (`assistant/policy.py`); anything not on it is blocked. The gate
  is enforced twice: the catalog filters what the model can *see*, and the
  router re-checks every call before it touches the pool.
- **Write tools = local admin power.** Enabling the sidebar toggle (or
  `DIST_DASHBOARD_WRITE_TOOLS=1` headless) lets the assistant run
  simulations, execute workflows, ingest graph data and export artifacts —
  treat it accordingly.
- **Secrets** come from env / `.streamlit/secrets.toml` only (git-ignored),
  never a UI input. `servers.yaml` contains no secrets.
- **Localhost only.** The server binds to `127.0.0.1` (`config.toml`); there
  is no auth in v1, so never expose the port. Tool results render via
  `st.json`/`st.code` (never markdown) so server output cannot execute as UI.

### Layout

- `app.py` — the Streamlit entry point (six views + sidebar).
- `data.py` — the data-access layer. Pure functions over the dist-stack APIs;
  the UI calls these, and the tests cover these.
- `styles.py` — the injected stylesheet and small HTML helpers (badges, cards).
- `assistant/` — the LLM chat assistant: `llm.py` (OpenAI client),
  `catalog.py` (tool catalog), `policy.py` (read-only allowlist),
  `router.py` (per-call execution + traces), `chat.py` (agent loop),
  `pool_runtime.py` (MCP pool on a thread), `prompt.py` (system prompt),
  `view.py` (Streamlit rendering).
- `tests/test_data.py` — smoke tests that seed temporary runstore / kg /
  registry databases through the dist-stack APIs and assert the data layer
  returns the expected rows.
- `tests/{test_catalog,test_policy,test_router,test_router_integration,test_chat}.py`
  — assistant tests (fake LLM + in-memory pool + one real MCP subprocess).

## Tests

```bash
# from apps/dist-dashboard
python -m unittest discover -s tests -v
```

## Design notes

- Neutral, warm-paper palette with a single dark-petrol accent; the only
  saturated colors are the semantic status badges (green = succeeded,
  red = failed, blue = running, gray = pending, orange = cancelled).
- Fraunces for headings, Inter for body, IBM Plex Mono for identifiers
  (run ids, paths, hashes) — with system fallbacks when offline.
- Tables use Streamlit's built-in sorting, search and row selection, so no
  custom table machinery was needed.
