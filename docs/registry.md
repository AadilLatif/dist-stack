# Registry Library (dist_stack.registry)

**What this is.** A versioned SQLite store keyed on `(model_id, version)` —
the single implementation of the `model_ref` resolution contract.
**When to use it.** When code needs to register, look up, or delete model
versions and resolve a `model_ref` to a stored path.

The registry is a versioned SQLite store keyed on **`(model_id, version)`**.
It is the single implementation of the `model_ref` resolution contract that
previously lived, copy-pasted, in `grid-data-models`, `gdm-flow`, and `erad`.

```{note}
Registry errors subclass `RegistryError(ValueError)`:
`InvalidModelRefError`, `ModelNotFoundError`, `ModelPathNotFoundError`,
`RegistryUnavailableError`, `HashMismatchError`.
```

## The `models` table contract

| Column | Type | Notes |
|---|---|---|
| `model_id` | `TEXT` | primary key half (with `version`) |
| `version` | `INTEGER` | primary key half; `next_version()` = `max+1`, else 1 |
| `stored_path` | `TEXT` | absolute by default; relative when `store_relative_to_db=True` |
| `model_hash` | `TEXT` | opaque string, stored as given |
| `metadata` | `TEXT` | JSON object, `{}` when NULL |
| `created_at_utc` | `TEXT` | ISO-8601 UTC |
| `deleted_at_utc` | `TEXT` | soft delete |

`models(model_id, version, stored_path)` is the load-bearing part of the
contract: every ecosystem resolver turns a `model_ref` into that stored path.

## Public API

### Register

```{eval-rst}
.. function:: register(model_id, version=None, stored_path=..., *, model_hash=None, hash_fn=None, metadata=None, registry_db=None, env_var="DIST_STACK_MODEL_REGISTRY_DB", check_exists=True, store_relative_to_db=False) -> ModelRecord

   Upsert a model row. ``version=None`` → ``next_version(model_id)``.
   Idempotent: identical ``(model_id, version, stored_path, model_hash)`` is a
   no-op; changed path/hash/metadata updates the row, clears ``deleted_at_utc``
   (resurrect), and preserves ``created_at_utc``.
   ``check_exists=True`` (default) raises :class:`ModelPathNotFoundError` if
   ``stored_path`` is missing. ``hash_fn`` (if given and ``model_hash`` is
   None) is called with the stored path string.
```

```python
from dist_stack import register

record = register(
    "my-model",
    stored_path="/abs/path/model.json",
    metadata={"tool": "save_system"},
    registry_db=reg_db,
)
```

### Lookup

```{eval-rst}
.. function:: lookup(model_id, version=None, *, registry_db=None, env_var=..., resolve_path=True, expected_hash=None) -> ModelRecord

   ``version=None`` → highest version among non-deleted rows.
   ``resolve_path=True`` resolves a relative ``stored_path`` against the DB
   file's parent. ``expected_hash`` set → :class:`HashMismatchError` on
   mismatch. Raises :class:`ModelNotFoundError` on miss.

.. function:: lookup_path(model_id, version=None, *, registry_db=None, env_var=...) -> str

   Convenience: ``str(lookup(..., resolve_path=False).stored_path)``.
```

```python
from dist_stack import lookup, lookup_path

record = lookup("my-model", registry_db=reg_db)        # latest
record = lookup("my-model", version=2, registry_db=reg_db)
path = lookup_path("my-model", registry_db=reg_db)     # stored path string
```

### Delete

```{eval-rst}
.. function:: delete(model_id, version=None, *, soft=True, registry_db=None, env_var=...) -> None

   ``soft=True`` stamps ``deleted_at_utc`` (idempotent re-delete);
   ``soft=False`` hard-deletes. ``version=None`` targets all versions of
   ``model_id``. Raises :class:`ModelNotFoundError` if no rows matched.
```

### List

```{eval-rst}
.. function:: list_models(*, include_deleted=False, registry_db=None, env_var=...) -> list[ModelRecord]

   All records ordered by ``model_id, version``; soft-deleted rows excluded
   unless ``include_deleted=True``.
```

### `model_ref` resolution

```{eval-rst}
.. function:: resolve_model_ref(model_ref, *, registry_db_env_var="DIST_STACK_MODEL_REGISTRY_DB") -> str

   Drop-in replacement for the three legacy resolvers. Path passthrough for
   ``("stored_path", "path", "source_path")``; else ``model_id`` (+ optional
   ``version``) looked up via the registry, returning the stored path verbatim.
   Resolution order: ``registry_db`` arg > ``model_ref["registry_db"]`` > env var.
```

```python
from dist_stack import resolve_model_ref

path = resolve_model_ref({"model_id": "my-model", "version": 2})
path = resolve_model_ref({"stored_path": "/direct/path.json"})  # passthrough
```

### Helpers

```{eval-rst}
.. function:: next_version(model_id, *, registry_db=None, env_var=...) -> int

   ``max(version)+1`` for ``model_id`` (including soft-deleted), else 1.

.. function:: make_model_id(source, *, namespace="dist-stack.models") -> str

   Deterministic id: ``str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{source}"))``.

.. function:: get_registry_path(registry_db=None, *, env_var=...) -> str

   Resolve the DB path (arg > env var), read lazily per call.
   Raises :class:`RegistryUnavailableError` when unset.

.. function:: ensure_schema(db_path) -> None

   Idempotent create/migrate; safe on every open.
```

## The `ModelRecord` dataclass

```python
@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    version: int
    stored_path: str                    # verbatim when resolve_path=False; absolute otherwise
    model_hash: str | None = None
    metadata: dict = field(default_factory=dict)
    created_at_utc: str | None = None   # ISO-8601 UTC
    deleted_at_utc: str | None = None
```

## Environment variable

`DIST_STACK_MODEL_REGISTRY_DB` — read lazily per call, never at import.
Resolution precedence:

    registry_db argument > model_ref["registry_db"] > DIST_STACK_MODEL_REGISTRY_DB

```python
import os
os.environ["DIST_STACK_MODEL_REGISTRY_DB"] = "/data/registry.sqlite"

record = lookup("my-model")   # picks up the env var on this call
```

## Migration

`PRAGMA user_version` is the schema-version authority. Legacy 3-column
`models` tables are migrated in place (additive `ALTER TABLE` + unique index)
with rows preserved — see `dist_stack.registry.schema.migrate`.

## Related

- {doc}`library` — the four stores at a glance.
- {doc}`manifest` — the provenance sidecar that names the artifact a registry
  entry points at.
- {doc}`ecosystem` — how the registry contract is shared across the domain
  servers.
