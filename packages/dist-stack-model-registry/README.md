# dist-stack-model-registry

Shared model registry library for the NREL distribution suites. A single
implementation of the `model_ref` resolution contract that previously lived,
copy-pasted, in `grid-data-models`, `gdm-flow`, and `erad`, plus a versioned
SQLite model registry (`register` / `lookup` / `delete` / `list_models`).

Zero runtime dependencies — stdlib only (`sqlite3`, `json`, `os`, `pathlib`,
`uuid`, `dataclasses`, `datetime`, `contextlib`, `threading`).
Requires Python >= 3.10 and SQLite >= 3.24 (Python >= 3.10 ships it).

## Install

Dev (editable, PEP 660 — never a manifest entry; part of the dist-stack
monorepo workspace):

    uv sync   # from the repo root; installs dist-stack-model-registry editable

CI/prod (from PyPI or the git URL):

    pip install "dist-stack-model-registry>=0.1,<1"

## Environment variable contract

`DIST_STACK_MODEL_REGISTRY_DB` — path to the SQLite registry database.

Resolution precedence, read **lazily per call** (never at import):

    explicit registry_db argument > model_ref["registry_db"] > DIST_STACK_MODEL_REGISTRY_DB

The env var may be set *after* import and still be honored on the next call.
When no path can be resolved, the library raises `RegistryUnavailableError`.

## Quick start

```python
from dist_stack import register, lookup, resolve_model_ref

# write side
record = register(
    model_id="my-model",
    stored_path="/abs/path/model.json",
    metadata={"tool": "save_system", "package": "grid-data-models"},
)

# read side: drop-in replacement for the legacy _resolve_model_ref_to_path
path = resolve_model_ref({"model_id": "my-model", "version": 2})

record = lookup("my-model")  # latest non-deleted version
```

## Notes

- The functional API is **stateless**: every call opens and closes its own
  `sqlite3` connection (WAL, `busy_timeout=5000`). Do *not* add
  shared-connection caching without an internal `threading.Lock`.
- `PRAGMA user_version` is the single schema-version authority; legacy
  3-column `models` tables are migrated in place (additive `ALTER TABLE` +
  unique index) with rows preserved.
- `model_hash` is an opaque string, stored as given; pass a `hash_fn` to
  `register` and/or an `expected_hash` to `lookup` for caller-driven
  verification. No verification happens by default.

## Development

Run the test suite from the source tree:

    PYTHONPATH=src python -m pytest tests/ -v
