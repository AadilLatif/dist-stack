"""Artifact attachment: sidecar read (fields copied verbatim), sidecar write
(config.run_id + derived_from), missing-path error, FK cascade on hard delete.
"""
from __future__ import annotations

import sqlite3

import pytest

from dist_stack import (
    ArtifactPathNotFoundError,
    ArtifactRecord,
    RunNotFoundError,
    attach_artifact,
    create_run,
    delete_run,
    list_artifacts,
)
from dist_stack.manifest import (
    get_manifest_path,
    has_manifest,
    read_manifest,
    write_manifest,
)


def make_artifact(tmp_path, name="out.json"):
    p = tmp_path / name
    p.write_text("{}")
    return p


def artifact_rows(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]


def test_attach_with_existing_sidecar_copies_fields_verbatim(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "sim_000000000001"
    create_run(
        "sim", run_type="erad_simulation", run_id=run_id,
        model_id="dist-stack.models:abc", model_version=3,
        model_hash="sha256:dead", runstore_db=db,
    )
    artifact = make_artifact(tmp_path, "results.json")
    write_manifest(
        artifact,
        artifact_type="custom_type",
        tool="custom_tool",
        tool_version="9.9.9",
        model_id="dist-stack.models:xyz",
        model_version=7,
        model_hash="sha256:beef",
        config={"k": "v"},
        derived_from=["/in/a.json"],
    )
    rec = attach_artifact(run_id, artifact, runstore_db=db)
    assert isinstance(rec, ArtifactRecord)
    assert rec.run_id == run_id
    assert rec.artifact_path == str(artifact)
    # copied verbatim from the sidecar, NOT from the run record
    assert rec.artifact_type == "custom_type"
    assert rec.tool == "custom_tool"
    assert rec.tool_version == "9.9.9"
    assert rec.model_id == "dist-stack.models:xyz"
    assert rec.model_version == 7
    assert rec.model_hash == "sha256:beef"
    assert rec.created_at_utc is not None
    # sidecar is untouched
    assert read_manifest(artifact).config == {"k": "v"}
    assert read_manifest(artifact).derived_from == ["/in/a.json"]
    arts = list_artifacts(run_id, runstore_db=db)
    assert len(arts) == 1
    assert arts[0] == rec


def test_attach_without_sidecar_writes_one(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "sim_000000000002"
    create_run(
        "sim", run_type="erad_simulation", run_id=run_id,
        tool_version="2.0.0", runstore_db=db,
    )
    artifact = make_artifact(tmp_path, "output.csv")
    rec = attach_artifact(run_id, artifact, runstore_db=db)
    assert has_manifest(artifact)
    m = read_manifest(artifact)
    assert m.artifact_type == "erad_simulation"      # from run.run_type
    assert m.tool == "sim"                            # from run.tool
    assert m.tool_version == "2.0.0"                  # from run.tool_version
    assert m.config == {"run_id": run_id}
    assert m.derived_from == [run_id]
    assert get_manifest_path(artifact).is_file()
    # row copies the just-written sidecar fields
    assert rec.artifact_type == m.artifact_type
    assert rec.tool == m.tool
    assert rec.artifact_path == str(artifact)


def test_attach_missing_path_raises(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "sim_000000000003"
    create_run("sim", run_type="erad_simulation", run_id=run_id, runstore_db=db)
    missing = tmp_path / "nope.json"
    with pytest.raises(ArtifactPathNotFoundError):
        attach_artifact(run_id, missing, runstore_db=db)
    assert not has_manifest(missing)
    assert list_artifacts(run_id, runstore_db=db) == []


def test_attach_missing_run_raises(tmp_path):
    db = tmp_path / "runstore.sqlite"
    artifact = make_artifact(tmp_path)
    with pytest.raises(RunNotFoundError):
        attach_artifact("ghost_000000000000", artifact, runstore_db=db)


def test_fk_cascade_on_hard_delete(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "sim_000000000004"
    create_run("sim", run_type="erad_simulation", run_id=run_id, runstore_db=db)
    attach_artifact(run_id, make_artifact(tmp_path, "a.json"), runstore_db=db)
    attach_artifact(run_id, make_artifact(tmp_path, "b.json"), runstore_db=db)
    assert len(list_artifacts(run_id, runstore_db=db)) == 2
    delete_run(run_id, soft=False, runstore_db=db)
    assert artifact_rows(db) == 0
    assert list_artifacts(run_id, runstore_db=db) == []


def test_same_artifact_attached_to_many_runs(tmp_path):
    db = tmp_path / "runstore.sqlite"
    artifact = make_artifact(tmp_path, "shared.json")
    for i in (5, 6):
        rid = f"sim_00000000000{i}"
        create_run("sim", run_type="erad_simulation", run_id=rid, runstore_db=db)
        attach_artifact(rid, artifact, runstore_db=db)
    assert artifact_rows(db) == 2
    assert len(list_artifacts("sim_000000000005", runstore_db=db)) == 1
    assert len(list_artifacts("sim_000000000006", runstore_db=db)) == 1
