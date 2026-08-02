"""Public API semantics: CRUD round-trips, status transitions, the `success`
property, payload JSON round-trip (incl. non-JSON coercion), soft delete +
`include_deleted`, the `list_runs` filter matrix, error cases, and id minting.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

import pytest

from dist_stack import (
    ArtifactRecord,
    RunExistsError,
    RunNotFoundError,
    RunRecord,
    RunstoreError,
    attach_artifact,
    create_run,
    delete_run,
    get_run,
    get_runstore_path,
    list_artifacts,
    list_runs,
    make_run_id,
    update_run,
)
from dist_stack.runstore import ensure_schema


def run_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def test_create_get_roundtrip(tmp_path):
    db = tmp_path / "runstore.sqlite"
    rec = create_run(
        "run_ac_opf",
        run_type="gdm_flow_run",
        run_id="ac_0123456789ab",
        status="succeeded",
        implementation="ac_opf",
        message="ok",
        tool_version="1.0.0",
        model_id="m1",
        model_version=2,
        model_hash="h",
        payload={"solver": "ac_opf"},
        runstore_db=db,
    )
    assert isinstance(rec, RunRecord)
    assert rec.run_id == "ac_0123456789ab"
    assert rec.tool == "run_ac_opf"
    assert rec.run_type == "gdm_flow_run"
    assert rec.status == "succeeded"
    assert rec.implementation == "ac_opf"
    assert rec.message == "ok"
    assert rec.tool_version == "1.0.0"
    assert rec.model_id == "m1"
    assert rec.model_version == 2
    assert rec.model_hash == "h"
    assert rec.payload == {"solver": "ac_opf"}
    assert rec.created_at_utc is not None
    assert rec.updated_at_utc is not None
    assert rec.deleted_at_utc is None
    got = get_run("ac_0123456789ab", runstore_db=db)
    assert got == rec


def test_create_mints_id_when_none(tmp_path):
    db = tmp_path / "runstore.sqlite"
    rec = create_run("sim", run_type="erad_simulation", runstore_db=db)
    assert re.match(r"^sim_[0-9a-f]{12}$", rec.run_id)
    assert get_run(rec.run_id, runstore_db=db) == rec


def test_create_mints_fallback_prefix_for_underscored_tool(tmp_path):
    db = tmp_path / "runstore.sqlite"
    rec = create_run("run_ac_opf", run_type="gdm_flow_run", runstore_db=db)
    assert re.match(r"^run_[0-9a-f]{12}$", rec.run_id)


def test_status_transitions_pending_running_succeeded(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "wf_1234567890ab"
    create_run(
        "workflow",
        run_type="workflow_execution",
        run_id=run_id,
        status="pending",
        runstore_db=db,
    )
    assert get_run(run_id, runstore_db=db).status == "pending"
    update_run(run_id, status="running", runstore_db=db)
    assert get_run(run_id, runstore_db=db).status == "running"
    update_run(run_id, status="succeeded", message="done", runstore_db=db)
    rec = get_run(run_id, runstore_db=db)
    assert rec.status == "succeeded"
    assert rec.message == "done"


def test_success_property(tmp_path):
    db = tmp_path / "runstore.sqlite"
    for status, expected in [
        ("succeeded", True),
        ("failed", False),
        ("cancelled", False),
        ("pending", None),
        ("running", None),
    ]:
        rid = f"r_{status}"
        create_run("t", run_type="rt", run_id=rid, status=status, runstore_db=db)
        assert get_run(rid, runstore_db=db).success is expected


def test_success_convenience_maps_to_status(tmp_path):
    db = tmp_path / "runstore.sqlite"
    ok = create_run(
        "sim",
        run_type="erad_simulation",
        run_id="sim_ok0000000001",
        success=True,
        runstore_db=db,
    )
    assert ok.status == "succeeded"
    assert ok.success is True
    bad = create_run(
        "sim",
        run_type="erad_simulation",
        run_id="sim_bad000000001",
        success=False,
        runstore_db=db,
    )
    assert bad.status == "failed"
    assert bad.success is False


def test_status_and_success_are_xor(tmp_path):
    db = tmp_path / "runstore.sqlite"
    with pytest.raises(RunstoreError):
        create_run(
            "t",
            run_type="rt",
            run_id="x_1234567890ab",
            status="succeeded",
            success=True,
            runstore_db=db,
        )


def test_status_literal_enforced(tmp_path):
    db = tmp_path / "runstore.sqlite"
    with pytest.raises(RunstoreError):
        create_run("t", run_type="rt", run_id="x_1234567890ab",
                   status="mystery", runstore_db=db)
    rid = "x_1234567890ab"
    create_run("t", run_type="rt", run_id=rid, status="pending", runstore_db=db)
    with pytest.raises(RunstoreError):
        update_run(rid, status="mystery", runstore_db=db)


def test_payload_json_roundtrip_with_non_json_coercion(tmp_path):
    db = tmp_path / "runstore.sqlite"
    when = datetime.now(timezone.utc)
    create_run(
        "sim",
        run_type="erad_simulation",
        run_id="sim_000000000001",
        payload={"when": when, "count": 3, "ok": True},
        runstore_db=db,
    )
    rec = get_run("sim_000000000001", runstore_db=db)
    assert rec.payload == {"when": str(when), "count": 3, "ok": True}
    # NULL payload reads back as {}
    rec2 = create_run("t", run_type="rt", run_id="r_000000000002", runstore_db=db)
    assert rec2.payload == {}


def test_soft_delete_lifecycle_and_include_deleted(tmp_path):
    db = tmp_path / "runstore.sqlite"
    create_run("t", run_type="rt", run_id="r_000000000001", runstore_db=db)
    delete_run("r_000000000001", runstore_db=db)
    with pytest.raises(RunNotFoundError):
        get_run("r_000000000001", runstore_db=db)
    assert list_runs(runstore_db=db) == []
    hidden = list_runs(include_deleted=True, runstore_db=db)
    assert [r.run_id for r in hidden] == ["r_000000000001"]
    assert hidden[0].deleted_at_utc is not None
    # soft-delete on a soft-deleted run is idempotent (re-stamps)
    delete_run("r_000000000001", runstore_db=db)
    # hard delete removes the row entirely
    delete_run("r_000000000001", soft=False, runstore_db=db)
    assert run_count(db) == 0


def test_list_runs_filter_matrix(tmp_path):
    db = tmp_path / "runstore.sqlite"
    specs = [
        dict(run_id="r1", tool="run_ac_opf", run_type="gdm_flow_run",
             status="succeeded", implementation="ac_opf", session_id="s1"),
        dict(run_id="r2", tool="run_ac_pf", run_type="gdm_flow_run",
             status="running", implementation="ac_pf", session_id="s2"),
        dict(run_id="r3", tool="run_simulation", run_type="erad_simulation",
             status="failed", implementation=None, session_id=None),
    ]
    for s in specs:
        create_run(
            s["tool"], run_type=s["run_type"], run_id=s["run_id"],
            status=s["status"], implementation=s["implementation"],
            session_id=s["session_id"], runstore_db=db,
        )
    all_ids = {r.run_id for r in list_runs(runstore_db=db)}
    assert all_ids == {"r1", "r2", "r3"}
    assert {r.run_id for r in list_runs(tool="run_ac_opf", runstore_db=db)} == {"r1"}
    assert {r.run_id for r in list_runs(run_type="gdm_flow_run", runstore_db=db)} == {"r1", "r2"}
    assert {r.run_id for r in list_runs(status="failed", runstore_db=db)} == {"r3"}
    assert {r.run_id for r in list_runs(implementation="ac_opf", runstore_db=db)} == {"r1"}
    assert {r.run_id for r in list_runs(session_id="s2", runstore_db=db)} == {"r2"}
    assert {r.run_id for r in list_runs(tool="run_ac_pf", status="running",
                                        runstore_db=db)} == {"r2"}
    assert list_runs(status="cancelled", runstore_db=db) == []


def test_list_runs_ordering_limit_offset(tmp_path):
    db = tmp_path / "runstore.sqlite"
    for i in range(5):
        create_run("t", run_type="rt", run_id=f"r_{i:012d}", runstore_db=db)
    ids = [r.run_id for r in list_runs(runstore_db=db)]
    assert len(ids) == 5
    # same-second creates tie-break by run_id DESC
    assert ids == [f"r_{i:012d}" for i in range(4, -1, -1)]
    page = list_runs(limit=2, offset=1, runstore_db=db)
    assert [r.run_id for r in page] == [f"r_{i:012d}" for i in (3, 2)]


def test_run_exists_error(tmp_path):
    db = tmp_path / "runstore.sqlite"
    create_run("t", run_type="rt", run_id="r_000000000001", runstore_db=db)
    with pytest.raises(RunExistsError):
        create_run("t", run_type="rt", run_id="r_000000000001", runstore_db=db)


def test_run_not_found_for_get_update_delete(tmp_path):
    db = tmp_path / "runstore.sqlite"
    with pytest.raises(RunNotFoundError):
        get_run("nope_000000000000", runstore_db=db)
    with pytest.raises(RunNotFoundError):
        update_run("nope_000000000000", status="succeeded", runstore_db=db)
    with pytest.raises(RunNotFoundError):
        delete_run("nope_000000000000", runstore_db=db)


def test_external_three_part_run_id_accepted(tmp_path):
    db = tmp_path / "runstore.sqlite"
    rec = create_run(
        "run_qsts",
        run_type="gdm_flow_run",
        run_id="qsts_ac_0123456789ab",
        implementation="qsts",
        runstore_db=db,
    )
    assert rec.run_id == "qsts_ac_0123456789ab"
    assert get_run("qsts_ac_0123456789ab", runstore_db=db).run_id == rec.run_id


def test_make_run_id_format():
    rid = make_run_id("sim")
    assert re.match(r"^sim_[0-9a-f]{12}$", rid)
    assert len(rid) == 16


def test_make_run_id_rejects_invalid_prefix():
    for bad in ("Sim", "sim_", "si m", "9sim", "", "sim-run", "sim.run", "_sim"):
        with pytest.raises(RunstoreError):
            make_run_id(bad)


def test_create_run_invalid_run_id_raises(tmp_path):
    db = tmp_path / "runstore.sqlite"
    with pytest.raises(RunstoreError):
        create_run("t", run_type="rt", run_id="", runstore_db=db)
    with pytest.raises(RunstoreError):
        create_run("t", run_type="rt", run_id="has space_1234567890", runstore_db=db)
    with pytest.raises(RunstoreError):
        create_run("t", run_type="rt", run_id="x" * 129, runstore_db=db)


def test_update_only_provided_kwargs_and_payload_replaces(tmp_path):
    db = tmp_path / "runstore.sqlite"
    created = create_run(
        "t", run_type="rt", run_id="r_000000000001", status="running",
        message="old", payload={"a": 1}, model_id="m1", runstore_db=db,
    )
    updated = update_run(
        "r_000000000001", status="succeeded", payload={"b": 2}, runstore_db=db
    )
    assert updated.status == "succeeded"
    assert updated.payload == {"b": 2}      # REPLACED
    assert updated.message == "old"          # untouched
    assert updated.model_id == "m1"          # untouched
    assert updated.updated_at_utc is not None
    assert updated.updated_at_utc == get_run(
        "r_000000000001", runstore_db=db
    ).updated_at_utc


def test_ensure_schema_and_path(tmp_path):
    db = tmp_path / "runstore.sqlite"
    assert get_runstore_path(runstore_db=db) == str(db)
    ensure_schema(db)
    assert run_count(db) == 0


def test_artifacts_roundtrip(tmp_path):
    db = tmp_path / "runstore.sqlite"
    run_id = "sim_000000000001"
    create_run("sim", run_type="erad_simulation", run_id=run_id, runstore_db=db)
    artifact = tmp_path / "out.json"
    artifact.write_text("{}")
    rec = attach_artifact(run_id, artifact, runstore_db=db)
    assert isinstance(rec, ArtifactRecord)
    arts = list_artifacts(run_id, runstore_db=db)
    assert [a.artifact_id for a in arts] == [rec.artifact_id]
