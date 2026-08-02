# 9. Model Registry Specification (dist-stack)

**Status:** Implementation-ready design (oracle-verified against all six repos' source).
**Date:** 2026-07-31

---

# Specification: `dist-stack` — Shared Model Registry Library (v1)

**Status:** Implementation-ready design.
**Verification basis:** all three in-repo implementations read directly (`grid-data-models/src/gdm/mcp/server.py:75-124`, `gdm-flow/src/gdm_flow/mcp/server.py:106-151`, `erad/src/erad/mcp/simulation.py:22-67`); test fixtures (`grid-data-models/tests/test_mcp_server.py:84-98`, `gdm-flow/tests/test_mcp_server.py:77-97`, `erad/tests/test_mcp_server.py:103-124`); `hashing_utils.py:30-44`; pyproject files of all six repos.

---

## 1. Package location & host

**Decision: new sibling repo `dist-stack` at `/home/aadillatif/Documents/GitHub/dist-stack`, package `dist_stack`, zero runtime dependencies.**

| Option | Verdict | Why |
|---|---|---|
| **New sibling repo `dist-stack`** | **✓ Chosen** | Independent versioning breaks the `==2.3.7` coupling deadlock (erad/ditto/shift pin gdm; a registry fix must never require a gdm release). The env-var prefix `DIST_STACK_*` already names this layer. The future "dist-stack service" adopts this repo as its backend library — the service *hosts* the library rather than the library living inside the service, so MCP servers can use it directly without a network hop. |
| Package inside `gdm-stack` | Rejected | `gdm-stack` is a deliverables workspace (only `docs/`, `.git`, `.opencode` — no `pyproject.toml`). Mixing a shipped library into a notes repo creates a second, confusing distribution channel and implies gdm-stack becomes a dependency of six repos. |
| Package inside `grid-data-models` | Rejected | Registry is cross-cutting infrastructure, not model-domain. Shipping it inside gdm would couple registry releases to gdm's cadence and force the three `==2.3.7`-pinned repos to upgrade gdm to get registry fixes. It also risks an import cycle direction (gdm already imports nothing like this; erad/ditto/shift import gdm — a gdm→registry import is fine, but registry must never import gdm; a sibling keeps that boundary structural). |

**Package naming:** distribution name `dist-stack-model-registry` (PyPI-legal, unambiguous); import namespace `dist_stack` with submodules (`dist_stack.registry` now; `dist_stack.manifest`, `dist_stack.kg` later). The `DIST_STACK_*` env prefix already reserves this namespace, and the future service grows into it without renames.

**Pip / editable strategy:**
- Dev: `pip install -e /home/aadillatif/Documents/GitHub/dist-stack` in each repo's venv (PEP 660 editable; backend-agnostic for consumers).
- Declared dependency (CI/prod): `dist-stack-model-registry>=0.1,<1` in each repo's `[project] dependencies`, resolved from PyPI or the git URL `git+https://github.com/NREL-Distribution-Suites/dist-stack@v0.1.0`.
- Constraint note: gdm-flow and erad use **setuptools** without `allow-direct-references`; gdm/ditto/shift use hatchling with it (`grid-data-models/pyproject.toml:97`, `ditto:118`, `shift:95`). Therefore: no repo pins a `file://` path dependency in `pyproject.toml` — editable installs are a dev-environment action only, never a manifest entry. Uniform rule for all six: declare the version range in pyproject; `pip install -e ../dist-stack` in dev.
- `requires-python = ">=3.10"`: erad and shift declare `>=3.10` (`erad/pyproject.toml`, `shift/pyproject.toml`); the library must import everywhere, so the floor is 3.10 (use `from __future__ import annotations`).
- Build backend: hatchling (matches 3 of 6 repos; simplest `src/` layout support).

---

## 2. Package + module layout

```
dist-stack/
├── pyproject.toml               # name="dist-stack-model-registry", hatchling, requires-python=">=3.10"
├── README.md
├── src/dist_stack/
│   ├── __init__.py              # re-exports public API + __version__
│   ├── version.py               # __version__ = "0.1.0"  (hatch dynamic version source)
│   ├── registry/
│   │   ├── __init__.py          # re-export public surface (see §3)
│   │   ├── errors.py            # exception hierarchy
│   │   ├── model.py             # ModelRecord dataclass
│   │   ├── schema.py            # DDL constants, migration, PRAGMA handling
│   │   ├── sqlite.py            # _connect()/query helpers (the only sqlite3 usage)
│   │   └── api.py               # register/lookup/delete/list_models/resolve_model_ref/next_version/make_model_id
│   └── mcp/                     # FUTURE (Phase 2): registry MCP server; empty __init__ now
├── tests/
│   ├── test_api.py              # register/lookup/delete/list semantics
│   ├── test_resolve_model_ref.py  # golden-compat tests vs the three legacy implementations
│   ├── test_legacy_schema.py    # migration of the 3-column DDL
│   ├── test_hash.py
│   └── test_thread_safety.py
└── docs/
```

**Public surface** (`dist_stack/__init__.py` re-exports): `register, lookup, lookup_path, delete, list_models, resolve_model_ref, next_version, make_model_id, ensure_schema, get_registry_path, ModelRecord`, and the exception classes. Nothing else is public. MCP exposure is explicitly deferred (§10).

---

## 3. Public API — exact signatures and semantics

```python
# dist_stack/registry/errors.py
class RegistryError(ValueError): ...
class InvalidModelRefError(RegistryError): ...
class ModelNotFoundError(RegistryError): ...
class ModelPathNotFoundError(RegistryError): ...
class RegistryUnavailableError(RegistryError): ...
class HashMismatchError(RegistryError): ...
```

**Decision: `RegistryError(ValueError)`.** All three legacy resolvers raise `ValueError` with exact messages; gdm's tool dispatcher catches `GDMMCPException` then generic `Exception` (`gdm/mcp/server.py:752-768`), but tests and callers may catch `ValueError` specifically. Subclassing `ValueError` guarantees zero behavior change at every existing catch site. The distinct subclasses give new callers (and the future MCP layer) precise handling.

```python
# dist_stack/registry/model.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    version: int
    stored_path: str          # verbatim stored value when resolve_path=False; absolute when resolve_path=True
    model_hash: str | None = None
    metadata: dict = field(default_factory=dict)   # parsed JSON, {} when NULL
    created_at_utc: str | None = None              # ISO-8601 UTC
    deleted_at_utc: str | None = None
```

```python
# dist_stack/registry/api.py
from collections.abc import Callable
import os
from pathlib import Path

DEFAULT_ENV_VAR = "DIST_STACK_MODEL_REGISTRY_DB"
SCHEMA_VERSION = 1

def get_registry_path(
    registry_db: str | os.PathLike | None = None,
    *,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Resolve the registry DB path: explicit arg > model_ref.registry_db > env var.
    Raises RegistryUnavailableError when unset."""

def ensure_schema(db_path: str | os.PathLike) -> None:
    """Idempotent create/migrate; safe to call on every open. See §4."""

def register(
    model_id: str,
    version: int | None = None,
    stored_path: str | os.PathLike = ...,
    *,
    model_hash: str | None = None,
    hash_fn: Callable[[str], str] | None = None,
    metadata: dict | None = None,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
    check_exists: bool = True,
    store_relative_to_db: bool = False,
) -> ModelRecord:
    """Upsert. version=None → next_version(model_id) (max+1, else 1).
    Idempotent: identical (model_id, version, stored_path, model_hash) → no-op,
    original created_at_utc preserved. Re-register with changed path/hash →
    UPDATE stored_path/model_hash/metadata, clear deleted_at_utc, preserve created_at_utc.
    check_exists=True → ModelPathNotFoundError if stored_path missing.
    hash_fn (if given and model_hash is None) is called with the stored path string;
    its return value is stored. Returns the stored ModelRecord."""

def lookup(
    model_id: str,
    version: int | str | None = None,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
    resolve_path: bool = True,
    expected_hash: str | None = None,
) -> ModelRecord:
    """version=None → highest version (ORDER BY version DESC LIMIT 1) among non-deleted.
    version is normalized with int(version); a non-numeric version raises
    ValueError exactly as the legacy code did. resolve_path=True resolves relative
    stored_path against the DB file's parent directory.
    expected_hash set → HashMismatchError if stored model_hash differs.
    Raises ModelNotFoundError on miss."""

def lookup_path(
    model_id: str,
    version: int | str | None = None,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Convenience: str(lookup(..., resolve_path=False).stored_path)."""

def delete(
    model_id: str,
    version: int | None = None,
    *,
    soft: bool = True,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> None:
    """soft=True: set deleted_at_utc (re-delete re-stamps, idempotent).
    soft=False: hard DELETE. version=None targets all versions of model_id.
    Raises ModelNotFoundError if no rows matched."""

def list_models(
    *,
    include_deleted: bool = False,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[ModelRecord]:
    """ORDER BY model_id, version. Excludes soft-deleted unless include_deleted."""

def resolve_model_ref(
    model_ref: dict,
    *,
    registry_db_env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """DROP-IN replacement for the three copy-pasted _resolve_model_ref_to_path
    functions. Exact behavioral clone — see §5.1 for the golden contract."""

def next_version(
    model_id: str,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> int:
    """max(version)+1 for model_id (including soft-deleted), else 1."""

def make_model_id(source: str | os.PathLike, *, namespace: str = "dist-stack.models") -> str:
    """Deterministic id: str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{source}")).
    For ditto/shift adoption: same source → same model_id across sessions."""
```

**Thread-safety contract:** the functional API is **stateless** — every call opens its own `sqlite3` connection via `_connect()` (context-managed, closed on return). This is safe for concurrent asyncio MCP tool calls with no locks, because no connection or cursor is shared across threads. SQLite WAL (`PRAGMA journal_mode=WAL`, best-effort) permits concurrent readers + one writer; `PRAGMA busy_timeout=5000` makes writers wait instead of failing. A connection-per-call costs microseconds; MCP lookup frequency (per tool call) makes this the right default over connection pooling. Document: *do not* add shared-connection caching without an internal `threading.Lock`.

---

## 4. SQLite schema

```sql
-- New databases (also created idempotently over legacy ones via IF NOT EXISTS)
CREATE TABLE IF NOT EXISTS models (
    model_id       TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    stored_path    TEXT    NOT NULL,
    model_hash     TEXT,
    metadata       TEXT,              -- JSON object: tool provenance etc.
    created_at_utc TEXT,
    deleted_at_utc TEXT,
    PRIMARY KEY (model_id, version)
);

-- Legacy migration (additive; run only when PRAGMA user_version < 1)
ALTER TABLE models ADD COLUMN model_hash     TEXT;   -- each guarded: except sqlite3.OperationalError: pass
ALTER TABLE models ADD COLUMN metadata       TEXT;
ALTER TABLE models ADD COLUMN created_at_utc TEXT;
ALTER TABLE models ADD COLUMN deleted_at_utc TEXT;

-- Uniqueness on legacy tables (new tables already have the PK index)
CREATE UNIQUE INDEX IF NOT EXISTS idx_models_model_id_version ON models(model_id, version);

-- Schema version marker
PRAGMA user_version = 1;
```

Decisions:
- **`PRAGMA user_version` is the single schema-version authority** (survives table drops; no column drift; works on legacy tables without ALTER). `SCHEMA_VERSION = 1`. Migration runs only when `user_version < 1`, so per-call overhead after first open is one PRAGMA read.
- **Legacy compatibility is a hard requirement, not a nicety:** all three test suites create the identical 3-column table `models(model_id TEXT NOT NULL, version INTEGER NOT NULL, stored_path TEXT NOT NULL)` with no PK (`gdm/tests/test_mcp_server.py:86-94`, `gdm-flow:80-88`, `erad:108-116`) and INSERT rows directly. The library must work against that table *in place*: additive `ALTER TABLE` + `CREATE UNIQUE INDEX IF NOT EXISTS` (a PK constraint cannot be added to an existing SQLite table without rebuild; a unique index enforces the same invariant). Because the ALTERs are additive, legacy rows are preserved.
- **Columns nullable by design** (including `created_at_utc`): keeps the migrated and fresh DDL column-identical; the library always writes values.
- **`metadata` is a JSON `TEXT` column**, populated by the library as `json.dumps(metadata)` — stores tool provenance (e.g. `{"tool": "save_system", "tool_version": "0.1.0", "package": "grid-data-models", "package_version": "2.3.7"}`). Generic column, no schema churn per use case.
- **No `updated_at` column** (YAGNI for v1): re-register preserves `created_at_utc`, which is the provenance-relevant fact; note as future extension.
- **Indexes:** only the unique index above. The "latest version" query (`WHERE model_id = ? ORDER BY version DESC LIMIT 1`) is served by the `(model_id, version)` index; no secondary index earns its keep at this scale.
- **`stored_path` storage:** default stores `os.path.abspath(str(stored_path))` (lexical normalization, no symlink resolution, no FS access). `store_relative_to_db=True` stores `os.path.relpath(stored_path, Path(db_path).parent)` — DB-portable when the DB sits next to (or above) the model store.
- **Env semantics:** `registry_db` arg > `model_ref["registry_db"]` > env var, read **lazily per call** (never at import): the existing tests set `os.environ["DIST_STACK_MODEL_REGISTRY_DB"]` *after* import and call tools (`gdm/tests/test_mcp_server.py:100-106`) — import-time capture would break them.

---

## 5. Behavioral contracts

### 5.1 `resolve_model_ref` — the golden drop-in contract
This function is the direct replacement for all three legacy resolvers. Its contract is a **verbatim behavioral clone**, pinned by golden tests:

1. **Path-key passthrough:** for each of `("stored_path", "path", "source_path")` in order: if value is a `str` and `value.strip()` is truthy → return it **verbatim** (no normalization, no existence check). Note the legacy code checks `isinstance(value, str)` — a `Path` object in these keys is *ignored* and falls through to the `model_id` branch. The clone must replicate this exactly (no widening to `os.PathLike`).
2. **model_id required:** missing/non-str → `InvalidModelRefError("model_ref must include a path or model_id")`.
3. **DB resolution:** `model_ref.get("registry_db")` first, then env var (`gdm-flow/docs/mcp/overview.md:67` documents exactly this precedence); missing → `RegistryUnavailableError("model_ref requires DIST_STACK_MODEL_REGISTRY_DB (or model_ref.registry_db) when path fields are not provided")`.
4. **Lookup:** `version` present → exact version (`int(version)` normalization; a non-numeric value raises `ValueError` exactly as legacy `int()` did); absent → latest by `ORDER BY version DESC LIMIT 1`. Miss → `ModelNotFoundError(f"model_ref not found for model_id={model_id}, {suffix}")` where `suffix` is `"latest"` or `f"version={version}"` (the legacy message interpolates the *raw* version value, not the int-cast — replicate).
5. **Return:** the stored path **verbatim** (`resolve_path=False` internally) — preserves the legacy contract where relative values are returned untouched; servers do their own existence checks (`gdm-flow/mcp/server.py:100-102` raises `FileNotFoundError` in `_load_system`; `erad/mcp/simulation.py:95-96` checks `file_path.exists()`).

Implementation shape (single source of truth):

```python
def resolve_model_ref(model_ref: dict, *, registry_db_env_var: str = DEFAULT_ENV_VAR) -> str:
    for key in ("stored_path", "path", "source_path"):
        value = model_ref.get(key)
        if isinstance(value, str) and value.strip():
            return value
    model_id = model_ref.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise InvalidModelRefError("model_ref must include a path or model_id")
    version = model_ref.get("version")
    db_path = model_ref.get("registry_db") or os.getenv(registry_db_env_var)
    if not db_path:
        raise RegistryUnavailableError(
            "model_ref requires DIST_STACK_MODEL_REGISTRY_DB (or model_ref.registry_db) "
            "when path fields are not provided"
        )
    try:
        record = lookup(model_id, version=version, registry_db=db_path, resolve_path=False)
    except ModelNotFoundError:
        suffix = "latest" if version is None else f"version={version}"
        raise ModelNotFoundError(f"model_ref not found for model_id={model_id}, {suffix}") from None
    return record.stored_path
```

### 5.2 Idempotency of `register`
- Same `(model_id, version, stored_path, model_hash)` → **no-op**: `INSERT ... ON CONFLICT(model_id, version) DO UPDATE` writes identical values; `created_at_utc` is *not* in the DO UPDATE set, so it is preserved. Returns the record. (Requires SQLite ≥ 3.24 upsert; Python ≥ 3.10 ships ≥ 3.37 — safe; note the floor in docs.)
- Same `(model_id, version)`, different `stored_path`/`model_hash`/`metadata` → **update** those columns and clear `deleted_at_utc` (re-register resurrects a soft-deleted row), preserving `created_at_utc`.
- `version=None` → auto-increment via `next_version` (max+1 **including soft-deleted rows** — prevents version reuse after delete, keeping history append-only).

### 5.3 Hash verification and `gdm.hashing_utils` integration
**Facts:** `hashing_utils.hash_model` (`grid-data-models/src/gdm/hashing_utils.py:30-44`) returns `hash(str(cleaned_model))` — builtin `hash()` on a str is **salted per process** (`PYTHONHASHSEED`). Cross-process comparison of stored gdm hashes is therefore unreliable.

**Decisions:**
- The registry treats `model_hash` as an **opaque string** — stored as given, never computed internally (the library has zero dependencies and cannot load gdm models).
- `register(..., hash_fn=...)` — callers may supply a path→str function; the library calls `hash_fn(stored_path)` when `model_hash` is not given and stores the result. gdm integration (future): `hash_fn=lambda p: str(gdm.hashing_utils.hash_model(DistributionSystem.from_json(p)))` — *document that this is same-process-only*, and prefer a deterministic `sha256(canonical_json)` hash_fn for anything cross-process.
- `lookup(..., expected_hash=...)` — the *verification* contract that works cross-process: compares stored vs expected (e.g., from a manifest); mismatch → `HashMismatchError`. **No verification on lookup by default** — preserves legacy lookup cost and behavior.
- Hash is **not required** at register (legacy DBs and path-only workflows must keep working).

### 5.4 Cross-process locking
- `PRAGMA journal_mode=WAL` on every connect, wrapped in `try/except sqlite3.OperationalError` (fails harmlessly on `:memory:` and read-only filesystems).
- `PRAGMA busy_timeout = 5000` on every connect. Writers serialize via SQLite itself; at this scale (registrations per workflow, lookups per tool call) no application-level locks are needed. Document that `sqlite3.OperationalError: database is locked` after 5 s indicates a pathological writer (e.g., a long transaction held open) — the library never holds transactions across calls, so it cannot be the cause.

### 5.5 Migration of existing ad-hoc implementations' data
**Reality check:** there is no production data to migrate — the only `INSERT INTO models` statements in the entire ecosystem are test fixtures. The migration contract is therefore: (a) *any* DB shaped like the legacy 3-column table (or the full schema) must open, migrate in place, and serve lookups; (b) rows are preserved; (c) the unique index is created without a table rebuild. A `migrate` test fixture reproduces the exact DDL from the three test files. Relative-path rows in legacy DBs were (hypothetically) CWD-relative; since none exist, the library defines the new semantics (DB-relative) without a compat shim — documented as a breaking change in the 0.1.0 changelog, with zero affected users.

---

## 6. Adoption checklist per repo

### 6.1 grid-data-models — `src/gdm/mcp/server.py`
- **Replace** the body of `_resolve_model_ref_to_path` (lines 75–124) with a delegation, keeping the wrapper (call sites at `:127-137` (`_get_system_path_arg`) and `:139-149` (`_get_system_paths_arg`) reference it):
  ```python
  from dist_stack.registry import register, make_model_id, resolve_model_ref  # top of file

  def _resolve_model_ref_to_path(model_ref: dict[str, Any]) -> str:
      """Resolve a model_ref payload to a concrete system JSON path.

      Path-carrying refs pass through; model_id/version resolve via the
      dist_stack model registry (DIST_STACK_MODEL_REGISTRY_DB).
      """
      return resolve_model_ref(model_ref)
  ```
- **Remove** `import sqlite3` (line 11) — verified: used *only* at lines 98–99 inside the replaced function.
- **Optional write-side (Phase 1):** `_save_system` (lines 1018–1038) gains optional `model_id`/`version` args; after `system.to_json(...)` (line 1034), when `os.getenv("DIST_STACK_MODEL_REGISTRY_DB")` is set:
  ```python
  record = register(
      model_id=args.get("model_id") or make_model_id(output_path),
      version=args.get("version"),
      stored_path=output_path,
      metadata={"tool": "save_system", "tool_version": __version__, "package": "grid-data-models"},
  )
  # append "model_id"/"version" keys to the return dict
  ```
  **Behavior change:** none when env unset (tool returns exactly what it returns today); when set, the return dict gains two keys (additive; JSON consumers unaffected).
- **Docs:** update the env-var contract paragraph at `docs/mcp/MCP_README.md:192` to name the library.
- **Tests:** the existing registry test (`tests/test_mcp_server.py:79-108`) passes **unchanged** — it builds the legacy 3-column table and the library migrates it in place. Add one test: `register()` via the library, then `_get_system_summary({"model_ref": {"model_id": ..., "version": ...}})` resolves.

### 6.2 gdm-flow — `src/gdm_flow/mcp/server.py`
- **Same replacement** at lines 106–151 (wrapper kept; `_get_system_path_arg` at `:154-164` unchanged):
  ```python
  from dist_stack.registry import resolve_model_ref
  def _resolve_model_ref_to_path(model_ref: dict[str, Any]) -> str:
      return resolve_model_ref(model_ref)
  ```
- **Remove** `import sqlite3` (line 10) — verified: used only at lines 125–126 inside the replaced function.
- **Do NOT touch** `src/gdm_flow/sqlite_export.py` — the runs-table provenance columns (`model_id`, `model_hash`, `gdm_version` added to `runs` at `sqlite_export.py:39-45`) are the *quick-win fixer's* file; ownership boundary prevents merge collisions (§9).
- **Docs:** `docs/mcp/overview.md:67` wording updated to "resolved via the dist-stack model registry library".
- **Tests:** existing suite (`tests/test_mcp_server.py:77-97`) passes unchanged; add one register→model_ref integration test.

### 6.3 erad — `src/erad/mcp/simulation.py`
- **Replace** lines 22–67, preserving the `Path` return type (gdm/gdm-flow return `str`; erad's wrapper converts):
  ```python
  from dist_stack.registry import resolve_model_ref

  def _resolve_model_ref_to_path(model_ref: dict) -> Path:
      """Resolve model_ref payload into a local file path."""
      return Path(resolve_model_ref(model_ref))
  ```
- **Remove** `import sqlite3` (line 8) — verified: used only at lines 41–42 inside the replaced function.
- **No file overlap** with erad quick-wins (`src/erad/default_fragility_curves/default_fragility_curves.py`, `src/erad/mcp/__init__.py`) — erad can adopt the library before or in parallel with its quick-wins.
- **Tests:** `tests/test_mcp_server.py:103-124` passes unchanged (note its path-key test at `:100-101` — `{"path": str(example)}` — exercises the passthrough branch; golden tests in the library cover the same).
- **Not in scope here:** `mcp/__init__.py` version alignment (`__version__ = "1.0.0"` vs package 0.1.14) is the quick-win fixer's item; the registry library is version-agnostic.

### 6.4 Breakage risk summary (all repos)
None functional, by construction: identical messages (golden-tested), identical resolution order, identical latest-version semantics, legacy-table compatibility, lazy env reads. Risks are limited to (a) forgetting to remove now-unused `sqlite3` imports (ruff F401 will flag), and (b) ditto/shift return-shape changes — explicitly designed as additive-only below.

---

## 7. ditto / shift migration path

Both are path-isolated today (no `model_ref` anywhere; confirmed by audit and source). Migration is **write-side first, read-side second**:

### ditto — `src/ditto/mcp/server.py`
- **Register on write** at both GDM-JSON emission points:
  - `export_gdm_json` (lines 369–372): after `system.to_json(out, overwrite=True)` (line 371), when env set:
    ```python
    register(model_id=make_model_id(name), stored_path=out,
             metadata={"tool": "export_gdm_json", "package": "NREL-ditto"})
    ```
  - `convert_model` `save_gdm` branch (lines 421–423): after `system.to_json(gdm_path, overwrite=True)`:
    ```python
    register(model_id=make_model_id(input_path), stored_path=gdm_path,
             metadata={"tool": "convert_model", "reader_type": reader_type, "package": "NREL-ditto"})
    ```
  - `make_model_id` gives deterministic ids per source (same source → same model_id across sessions; version auto-increments on re-convert). **No return-shape changes** — these tools return plain strings; registry writes are side effects (avoids breaking tests asserting exact strings). Flag for the fixer: verify no ditto test asserts the exact `export_gdm_json`/`convert_model` return strings *with the env var set* (none currently do — the env var is only used in gdm/gdm-flow/erad tests).
- **Read-side (Phase 2, follow-up):** add optional `model_ref: dict | None = None` params to `load_gdm_json` / `get_system_summary` / `get_component_detail`, resolved via `resolve_model_ref` — strictly additive; path params keep working.
- **Behavior change:** none when env unset.

### shift — `src/shift/mcp_server/tools/system/export.py`
- **Register on write** in `export_system_json` (after line 53, `system.to_json(out)`), when env set:
  ```python
  register(model_id=make_model_id(system_name), stored_path=str(out),
           metadata={"tool": "export_system_json", "package": "nrel-shift"})
  ```
  (model_id from `system_name` — deterministic per named system; document the collision caveat that two sessions building the same name overwrite versions).
- **Return-shape change:** additive only — when registered, add `"model_id"`/`"version"` keys to the JSON dict (JSON consumers tolerate new keys). Flag for the fixer: verify `shift/tests/test_mcp_server/` fixtures don't assert exact JSON equality with the env set (audit: env var never appears in shift tests).
- **Non-MCP writes** (`src/shift/ui_api/app.py:372,1029`) are out of scope for the MCP migration; note as optional Phase 2 (the UI can reuse `register` directly).
- **Read-side (Phase 2):** shift tools accept `system_name` (in-memory `AppContext`) — registry integration there means a new tool (e.g., `load_system_from_registry(model_id)`), not param surgery. Defer.

---

## 8. Test strategy

**Library unit tests (stdlib-only pytest):**
1. `test_api.py` — register/lookup round-trip; latest-version selection (3 versions → highest); idempotent re-register (row count 1, `created_at_utc` preserved); update-on-change; auto-version sequence 1→2→3; `check_exists` → `ModelPathNotFoundError`; soft delete (lookup misses, `list_models` excludes, `include_deleted=True` shows, re-register resurrects); hard delete; `version` normalization incl. `int("2")` string.
2. `test_legacy_schema.py` — **golden fixture**: reproduce the exact DDL from `grid-data-models/tests/test_mcp_server.py:86-94` (and the gdm-flow/erad copies, byte-identical), INSERT a row, then: `user_version` becomes 1, columns exist via `PRAGMA table_info(models)`, unique index exists, `lookup` returns the legacy row verbatim, row count unchanged (migration preserved data).
3. `test_resolve_model_ref.py` — **golden-compat suite**: for each of the three legacy implementations, capture its exact behavior (path passthrough incl. `Path`-object non-passthrough; all four error messages verbatim; `registry_db` override precedence; env fallback; latest-vs-exact version; raw-version interpolation in the miss message) and assert the library matches byte-for-byte. These tests are the contract that makes adoption risk-free.
4. `test_hash.py` — store opaque hash; `lookup(expected_hash=...)` match/mismatch; `hash_fn` invoked with stored path; hash optional.
5. `test_thread_safety.py` — `ThreadPoolExecutor(8)` × 200 mixed register/lookup against one file DB: no exceptions, correct final row counts.
6. `test_env_laziness.py` — set `DIST_STACK_MODEL_REGISTRY_DB` *after* import (mirrors `gdm/tests/test_mcp_server.py:100-106`), verify resolution works; unset → `RegistryUnavailableError`.
7. Path-portability test: register with `store_relative_to_db=True`, move the DB + model file to a new directory, lookup still resolves (the property that motivated relative storage).

**Per-repo integration:**
- Run each repo's existing `tests/test_mcp_server.py` **before and after** adoption — they must pass identically (they self-create legacy DBs; the library migrates them transparently). This is the primary "nothing broke" gate.
- Add one test per repo: library `register()` → MCP tool call with `{"model_ref": {"model_id": ..., "version": ...}}` → correct result. For gdm: `_get_system_summary`; gdm-flow: `_get_system_path_arg`; erad: `_resolve_model_ref_to_path`; ditto/shift: the register-on-write tools with env set (assert side-effect row exists + return shape unchanged).
- **Verification without behavior change:** diff the before/after pytest outputs; additionally a manual smoke of each MCP server with a `model_ref`-carrying tool call. No golden-message changes are permitted — the library's golden tests enforce this at the source.

---

## 9. Sequencing & coordination with the quick-win fixers

**File-ownership map (verified):**

| File | Quick-win fixer | Registry adoption |
|---|---|---|
| `grid-data-models/src/gdm/mcp/server.py` | **yes** (manifest drift, dead params) | **yes** (`_resolve_model_ref_to_path` :75-124) |
| `grid-data-models/src/gdm/mcp/knowledge/documentation.py` | yes | no |
| `grid-data-models/src/gdm/mcp/validation/diagnostics.py` | yes | no |
| `gdm-flow/src/gdm_flow/mcp/server.py` | **yes** (docs/tool drift) | **yes** (:106-151) |
| `gdm-flow/src/gdm_flow/sqlite_export.py` | yes (enums, runs provenance) | no |
| `erad/src/erad/default_fragility_curves/default_fragility_curves.py` | yes | no |
| `erad/src/erad/mcp/__init__.py` | yes (version) | no |
| `erad/src/erad/mcp/simulation.py` | no | **yes** (:22-67) |
| `ditto/src/ditto/mcp/server.py`, `shift/.../system/export.py` | docs-only | **yes** (write-side) |

**Recommended order (collision-free):**

1. **Week 1 — parallel, zero overlap:** (a) scaffold the `dist-stack` repo: pyproject, schema, API, golden-compat tests — this touches *nothing* in the six repos; (b) erad quick-wins (its files never intersect registry adoption).
2. **Week 2 — erad registry adoption first.** No overlap with its quick-wins; smallest server; proves the library against a live MCP server. Also parallel: ditto + shift write-side adoption (no quick-win overlap; strictly additive).
3. **Week 2–3 — gdm-flow, serialized:** quick-win PR (server.py docs/manifest + sqlite_export.py enums) **merges first**, then registry-adoption PR on `mcp/server.py`. Two fixers on one file must not run concurrently — if parallel execution is forced, the registry adoption is the one that waits, because it replaces a function the quick-wins only touch incidentally, and its golden tests make it trivially re-basable.
4. **Week 3–4 — gdm last, serialized:** quick-win PRs (server.py, documentation.py, diagnostics.py) merge first; then registry adoption on `server.py` + optional `_save_system` register. gdm is last because it has the largest `model_ref` surface and the most tools exercising the resolver.
5. **Week 4 — docs sweep:** update `MCP_README.md:192` (gdm), `overview.md:67` (gdm-flow), erad docs; add the dependency `dist-stack-model-registry>=0.1,<1` to all six pyprojects **in the same PR as each repo's code adoption** (single commit per repo, atomic and reviewable).
6. **Week 5 — follow-ups:** deterministic `hash_fn` (sha256) helper; `dist_stack.mcp` registry server (Phase 2); gdm-flow `runs` provenance columns land with the quick-win fixer and link to `model_id` via the registry.

---

## 10. Deferred / explicit non-goals (v1)
- **MCP exposure** (`dist_stack.mcp`): deferred to Phase 2 (registry list/register/delete tools or a standalone registry MCP server). The functional API is the MCP servers' integration point now.
- **Deterministic hashing:** the sha256-of-canonical-JSON helper is a v1.1 item; v1 stores opaque hashes and verifies only caller-supplied expectations.
- **Auto-registration inside gdm core** (`save_system` at the library level): registration stays an explicit MCP-tool-layer action so core serialization semantics never depend on env vars.
- **Registry-as-service:** the library is the backend of the future dist-stack service; no RPC layer in v1.
- **Auto-version naming schemes, tags, aliases:** YAGNI until a consumer demonstrates the need.

**Assumptions flagged:** (1) `DIST_STACK_MODEL_REGISTRY_DB` remains the canonical env var — preserved as the library default; (2) all six repos are installed into environments where a sibling editable install is acceptable (dev) or the PyPI/git dependency resolves (CI/prod); (3) no external production registry DBs exist today — supported by the finding that every `INSERT INTO models` in the ecosystem is a test fixture.
