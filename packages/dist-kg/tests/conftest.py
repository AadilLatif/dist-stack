"""Shared fixtures: tmp KG/runstore/registry DBs + a seeded fixture graph.

``kg_db`` creates a fresh, schema-initialised KG DB and monkeypatches
``DIST_STACK_KG_DB`` to point at it (per-test isolation). ``seed_kg`` populates
a deterministic fixture graph: one ``gdm_flow_run`` producing three artifacts
with a derived-from chain (a3 -> a2 -> a1) and one artifact -> model reference
edge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dist_stack.kg import ensure_schema, upsert_edge, upsert_node
from dist_stack.kg.api import DEFAULT_ENV_VAR
from dist_stack.registry import ensure_schema as ensure_registry_schema
from dist_stack.runstore import ensure_schema as ensure_runstore_schema

# Fixture graph constants (node ids follow the ``artifact:<normpath>`` scheme).
A1 = "/data/outputs/a1.json"
A2 = "/data/outputs/a2.json"
A3 = "/data/outputs/a3.json"


@pytest.fixture
def runstore_db(tmp_path: Path) -> Path:
    """A fresh, empty runstore DB path (schema created on first use)."""
    db = tmp_path / "runstore.db"
    ensure_runstore_schema(db)
    return db


@pytest.fixture
def registry_db(tmp_path: Path) -> Path:
    """A fresh, empty model-registry DB path (schema created on first use)."""
    db = tmp_path / "registry.db"
    ensure_registry_schema(db)
    return db


@pytest.fixture
def kg_db(tmp_path: Path, monkeypatch) -> Path:
    """A fresh, schema-initialised KG DB, wired via DIST_STACK_KG_DB."""
    db = tmp_path / "kg.db"
    ensure_schema(db)
    monkeypatch.setenv(DEFAULT_ENV_VAR, str(db))
    return db


@pytest.fixture
def seed_kg(kg_db: Path) -> Path:
    """The fixture graph (see module docstring), seeded via the store API."""
    # -- nodes ----------------------------------------------------------------
    upsert_node(
        "run:r1", "gdm_flow_run", label="gdm_flow r1", run_id="r1",
        tool="gdm_flow", tool_version="1.0.0", kg_db=kg_db,
    )
    upsert_node(
        "run:r2", "workflow_execution", label="wf_abc123", run_id="r2",
        tool="run_workflow", tool_version="0.1.0", kg_db=kg_db,
    )
    for path in (A1, A2, A3):
        upsert_node(
            f"artifact:{path}", "artifact", label=path.rsplit("/", 1)[-1],
            artifact_path=path, run_id="r1", kg_db=kg_db,
        )
    upsert_node("model:m1", "model", label="m1", model_id="m1", kg_db=kg_db)

    # -- edges ----------------------------------------------------------------
    for path in (A1, A2, A3):
        upsert_edge(
            "run:r1", f"artifact:{path}", "has_artifact",
            metadata={"tool": "gdm_flow", "tool_version": "1.0.0"}, kg_db=kg_db,
        )
    upsert_edge(f"artifact:{A1}", "run:r1", "generated_by", kg_db=kg_db)
    upsert_edge(f"artifact:{A2}", f"artifact:{A1}", "derived_from", kg_db=kg_db)
    upsert_edge(f"artifact:{A3}", f"artifact:{A2}", "derived_from", kg_db=kg_db)
    upsert_edge(
        f"artifact:{A1}", "model:m1", "references",
        metadata={"model_id": "m1"}, kg_db=kg_db,
    )
    return kg_db
