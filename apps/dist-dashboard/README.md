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

## Layout

- `app.py` — the Streamlit entry point (five views + sidebar).
- `data.py` — the data-access layer. Pure functions over the dist-stack APIs;
  the UI calls these, and the tests cover these.
- `styles.py` — the injected stylesheet and small HTML helpers (badges, cards).
- `tests/test_data.py` — smoke tests that seed temporary runstore / kg /
  registry databases through the dist-stack APIs and assert the data layer
  returns the expected rows.

## Tests

```bash
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
