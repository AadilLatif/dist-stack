"""Env var read lazily per call, never at import.

``DIST_STACK_RUNSTORE_DB`` is set *after* the library has already been
imported and must be honored on the next call; unset → RunstoreUnavailableError
at call time only.
"""
from __future__ import annotations

import pytest

import dist_stack  # noqa: F401  (imported BEFORE env is set — the point)
from dist_stack import (
    RunNotFoundError,
    RunstoreUnavailableError,
    attach_artifact,
    create_run,
    delete_run,
    get_run,
    get_runstore_path,
    list_artifacts,
    list_runs,
    update_run,
)


def test_env_set_after_import_resolves(tmp_path, monkeypatch):
    db = tmp_path / "runstore.sqlite"
    monkeypatch.setenv("DIST_STACK_RUNSTORE_DB", str(db))
    rec = create_run("sim", run_type="erad_simulation", run_id="sim_000000000001")
    assert rec.run_type == "erad_simulation"
    assert get_run("sim_000000000001").run_id == "sim_000000000001"


def test_env_unset_raises_at_call_time(tmp_path, monkeypatch):
    monkeypatch.delenv("DIST_STACK_RUNSTORE_DB", raising=False)
    with pytest.raises(RunstoreUnavailableError):
        get_runstore_path()
    with pytest.raises(RunstoreUnavailableError):
        create_run("t", run_type="rt")
    with pytest.raises(RunstoreUnavailableError):
        get_run("anything_0000000000")
    with pytest.raises(RunstoreUnavailableError):
        list_runs()
    with pytest.raises(RunstoreUnavailableError):
        update_run("anything_0000000000", status="succeeded")
    with pytest.raises(RunstoreUnavailableError):
        delete_run("anything_0000000000")
    with pytest.raises(RunstoreUnavailableError):
        list_artifacts("anything_0000000000")


def test_attach_artifact_env_lazy(tmp_path, monkeypatch):
    monkeypatch.delenv("DIST_STACK_RUNSTORE_DB", raising=False)
    artifact = tmp_path / "out.json"
    artifact.write_text("{}")
    with pytest.raises(RunstoreUnavailableError):
        attach_artifact("sim_000000000001", artifact)


def test_env_read_lazily_per_call(tmp_path, monkeypatch):
    db1 = tmp_path / "one.sqlite"
    db2 = tmp_path / "two.sqlite"
    create_run("t", run_type="rt", run_id="a_000000000001", runstore_db=db1)
    create_run("t", run_type="rt", run_id="b_000000000001", runstore_db=db2)

    monkeypatch.setenv("DIST_STACK_RUNSTORE_DB", str(db1))
    assert get_run("a_000000000001").run_id == "a_000000000001"
    with pytest.raises(RunNotFoundError):
        get_run("b_000000000001")

    # Switching the env var between calls must take effect immediately.
    monkeypatch.setenv("DIST_STACK_RUNSTORE_DB", str(db2))
    assert get_run("b_000000000001").run_id == "b_000000000001"
    with pytest.raises(RunNotFoundError):
        get_run("a_000000000001")

    monkeypatch.delenv("DIST_STACK_RUNSTORE_DB")
    with pytest.raises(RunstoreUnavailableError):
        get_run("b_000000000001")


def test_empty_env_var_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("DIST_STACK_RUNSTORE_DB", "")
    with pytest.raises(RunstoreUnavailableError):
        get_runstore_path()
