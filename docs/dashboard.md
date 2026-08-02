# Dashboard App (dist-dashboard)

**Backing libraries:** {doc}`runstore`, {doc}`kg`, {doc}`registry` — the three
stores this app reads through the dist-stack Python APIs.

`apps/dist-dashboard` is the **human-review tier** of the dist-stack
monorepo: a read-only [Streamlit](https://streamlit.io) browser over the three
shared stores — the runstore, the knowledge graph, and the model registry.
Where the MCP servers (the runner, dist-kg) expose those stores to an agent,
the dashboard exposes them to a person.

Everything in the app is read through the dist-stack Python APIs
(`dist_stack.runstore`, `dist_stack.kg`, `dist_stack.registry`) — it never
opens the SQLite files directly and never writes to them. It also never
creates a database file: if a store's DB file does not exist, the app renders
an empty state instead of crashing or initializing the schema.

## The five views

The sidebar navigates between five views (`PAGES` in `app.py`):

| View | What it shows |
|---|---|
| **Overview** | The recorded state at a glance: the three data sources (with their resolved paths), counts per store, recent runs, and the most-connected graph nodes. |
| **Run History** | The runstore table, newest first, with run-type / tool / status filters and pagination (25 rows per page). Selecting a run opens its detail view — message, payload, model fields, and attached artifacts. |
| **Provenance** | Pick a node (by id, or by searching a run id / artifact path / model id) and trace its ancestry (upstream) or descendancy (downstream) as an indented tree, plus an immediate-neighbor table. |
| **Knowledge Graph** | What the graph currently holds: node and edge counts by type/relation, and a node search by type and label. |
| **Registry** | Registered model versions with their stored paths and hashes, filterable by model id. |

The UI is deliberately thin: `app.py` renders, `data.py` is the pure
data-access layer (the seam the smoke tests exercise), and `styles.py` holds
the injected stylesheet and small HTML helpers.

## Running it

```bash
# from the monorepo root
uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py
```

The app is also wired for headless use (e.g. a CI or remote box):

```bash
uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py \
  --server.headless=true --server.port=8599
```

Open the printed URL (default `http://localhost:8501`).

## Database resolution

Each store's DB path resolves in this order
(`data.resolve_db_path`): **sidebar override > environment variable >**
`~/.cache/dist-stack/<default filename>`:

| Store | Env var | Default file |
|---|---|---|
| Runstore | `DIST_STACK_RUNSTORE_DB` | `~/.cache/dist-stack/runstore.db` |
| Knowledge graph | `DIST_STACK_KG_DB` | `~/.cache/dist-stack/kg.db` |
| Model registry | `DIST_STACK_MODEL_REGISTRY_DB` | `~/.cache/dist-stack/registry.db` |

The sidebar Settings panel lets you override any of the three paths at
runtime; an override wins over the environment variable for that session. A
store counts as available only if its file actually exists
(`data.db_available`), and every read is guarded so a missing or unreadable
store renders an empty state rather than an exception.

## Read-only contract

- No writes: the app never calls a `create_*` / `upsert_*` / `delete_*` API,
  never opens a DB file in write mode, and never creates a missing DB file.
- No setup: the dashboard expects the stores to be populated by the tools that
  write them (domain servers via the runstore / manifest hooks, the KG via
  `dist_stack.kg.ingest` or the `dist-kg` server's `ingest` tool, the registry
  via `register`). See {doc}`ecosystem` for how data flows into those stores.

## Testing

The smoke tests seed throwaway runstore / kg / registry databases through the
dist-stack APIs and assert the data layer returns the expected rows (the UI
framework is deliberately not exercised):

```bash
cd apps/dist-dashboard && uv run --project . python -m unittest discover -s tests -v
```

See {doc}`runner` and {doc}`kg-server` for the MCP servers that populate the
stores this dashboard reads, and {doc}`mcp-wiring` for the full ecosystem
wiring.

## Related

- {doc}`usage-scenarios` — Journey 2 shows how to see and trace results (a
  human-facing alternative to the graph queries).
- {doc}`runstore` / {doc}`kg` / {doc}`registry` — the backing stores.
