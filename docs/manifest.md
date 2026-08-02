# Manifest Library (dist_stack.manifest)

**What this is.** An immutable JSON **sidecar** written next to every artifact,
recording what produced it, from what, and when.
**When to use it.** When code needs to record provenance at the file level —
the authority the KG ingester reads for `derived_from` and config.

A **manifest** is an immutable (frozen) JSON **sidecar** written next to every
artifact, recording what produced it, from what, and when. It is the authority
the knowledge-graph ingester reads for `derived_from` and config.

Manifests are pure file I/O — no registry or env access, no database.

## Sidecar location

The sidecar lives at **`{artifact_path}{MANIFEST_SUFFIX}`** where
`MANIFEST_SUFFIX = ".manifest.json"`:

```python
from dist_stack import get_manifest_path, has_manifest, MANIFEST_SUFFIX

get_manifest_path("/data/out.json")   # Path('/data/out.json.manifest.json')
has_manifest("/data/out.json")        # False — sidecar not present yet
```

## The `Manifest` dataclass

```{eval-rst}
.. class:: Manifest
```

Required fields (declared before optional ones — a dataclass constraint):

| Field | Type | Meaning |
|---|---|---|
| `artifact_path` | `str` | path to the artifact this describes |
| `artifact_type` | `str` | e.g. `gdm_system`, `gdm_flow_run`, `erad_simulation`, ... |
| `tool` | `str` | tool name that created the artifact |
| `tool_version` | `str` | tool version |

Optional fields:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `int` | `MANIFEST_SCHEMA_VERSION` (1) |
| `model_id` | `str \| None` | registry identity |
| `model_version` | `int \| None` | registry version |
| `model_hash` | `str \| None` | opaque hash string |
| `package` | `str \| None` | package name |
| `package_version` | `str \| None` | package version |
| `config` | `dict` | snapshot of relevant config |
| `derived_from` | `list[str]` | parent artifact paths / run ids / URIs |
| `created_at_utc` | `str` | ISO-8601 UTC, default `now()` |
```

## Public API

```{eval-rst}
.. function:: write_manifest(artifact_path, **kwargs) -> Manifest

   Create a :class:`Manifest` from kwargs and write it as a JSON sidecar next
   to ``artifact_path`` (the artifact itself need not exist yet — it may be
   about to be created). ``artifact_path`` defaults to ``str(artifact_path)``
   unless overridden. Serialization: ``json.dumps(..., indent=2,
   ensure_ascii=False, default=str)``. Returns the frozen manifest.

.. function:: read_manifest(artifact_path) -> Manifest

   Read and return the sidecar. Raises ``FileNotFoundError`` if none exists.
   A ``schema_version`` mismatch is warned about but does not fail the read.

.. function:: has_manifest(artifact_path) -> bool

   Whether a sidecar exists next to ``artifact_path``.

.. function:: get_manifest_path(artifact_path) -> Path

   Expected sidecar path: ``{artifact_path}{MANIFEST_SUFFIX}``.
```

## Usage pattern

```python
from pathlib import Path
from dist_stack import write_manifest, read_manifest, has_manifest

artifact = Path("/data/result.json")

manifest = write_manifest(
    artifact,
    artifact_type="erad_simulation",
    tool="run_simulation",
    tool_version="0.3.0",
    model_id="my-model",
    model_version=3,
    model_hash="sha256:...",
    config={"hazard_system_id": "h-1"},
    derived_from=["/data/base.json", "sim_000000000001"],
)

# later...
if has_manifest(artifact):
    manifest = read_manifest(artifact)
    print(manifest.derived_from)   # ['/data/base.json', 'sim_000000000001']
    print(manifest.config)         # {'hazard_system_id': 'h-1'}
```

## `derived_from` resolution rules (as consumed by the KG ingester)

When `dist_stack.kg.ingest` reads a sidecar's `derived_from`, each entry is
resolved in order (see {doc}`kg`):

1. **Artifact node** — the entry normalizes to an existing
   `artifact:<normpath(path)>` node → `derived_from` edge.
2. **Run fallback** — the entry matches a known `run:<run_id>` node →
   `derived_from` edge to the run.
3. **URI** — the entry contains `://` → no edge; counted in
   `IngestReport.derived_from_uri_skipped` and kept in node metadata as
   `derived_from_raw`.
4. **Unresolvable** — otherwise → no edge; appended to
   `IngestReport.derived_from_unresolved`.

Self-loops (an entry pointing at the artifact's own path) are skipped.

```{note}
When `runstore.attach_artifact` is called with **no** existing sidecar, it
writes one with ``derived_from=[run_id]`` — the "attach fallback" that makes
the run the recorded parent.
```

## Related

- {doc}`library` — the four stores at a glance.
- {doc}`runstore` — `attach_artifact` consumes these sidecars.
- {doc}`kg` — the ingester reads `derived_from` from these sidecars.
- {doc}`ecosystem` — the provenance spine that ties sidecars, runs, and the KG
  together.
