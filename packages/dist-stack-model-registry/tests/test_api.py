"""Public API semantics: register/lookup/delete/list (§8 item 1)."""
from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from dist_stack import (
    ModelNotFoundError,
    ModelPathNotFoundError,
    ModelRecord,
    RegistryUnavailableError,
    delete,
    ensure_schema,
    get_registry_path,
    list_models,
    lookup,
    lookup_path,
    make_model_id,
    next_version,
    register,
)


def make_file(tmp_path, name="model.json", text="{}"):
    p = tmp_path / name
    p.write_text(text)
    return p


def row_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]


def test_register_lookup_roundtrip(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    rec = register("abc123", stored_path=f, registry_db=db)
    assert isinstance(rec, ModelRecord)
    assert rec.model_id == "abc123"
    assert rec.version == 1
    assert rec.stored_path == os.path.abspath(str(f))
    assert rec.created_at_utc is not None
    got = lookup("abc123", registry_db=db)
    assert got == rec


def test_register_accepts_pathlike(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    rec = register("m", stored_path=Path(f), registry_db=Path(db))
    assert rec.stored_path == os.path.abspath(str(f))


def test_latest_version_selection(tmp_path):
    db = tmp_path / "registry.sqlite"
    for v, name in ((1, "a.json"), (2, "b.json"), (3, "c.json")):
        register("m", version=v, stored_path=make_file(tmp_path, name), registry_db=db)
    assert lookup("m", registry_db=db).version == 3
    assert lookup("m", version=1, registry_db=db).version == 1
    assert lookup("m", version=2, registry_db=db).version == 2
    assert lookup("m", version="2", registry_db=db).version == 2


def test_idempotent_reregister_preserves_created_at(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    r1 = register("abc", version=1, stored_path=f, registry_db=db)
    r2 = register("abc", version=1, stored_path=f, registry_db=db)
    assert row_count(db) == 1
    assert r1.created_at_utc == r2.created_at_utc
    assert r1 == r2


def test_update_on_change(tmp_path):
    db = tmp_path / "registry.sqlite"
    f1 = make_file(tmp_path, "one.json")
    f2 = make_file(tmp_path, "two.json")
    r1 = register("abc", version=1, stored_path=f1, registry_db=db, model_hash="h1")
    r2 = register("abc", version=1, stored_path=f2, registry_db=db, model_hash="h2")
    assert row_count(db) == 1
    got = lookup("abc", registry_db=db)
    assert got.stored_path == os.path.abspath(str(f2))
    assert got.model_hash == "h2"
    assert got.created_at_utc == r1.created_at_utc  # preserved on update


def test_auto_version_sequence(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    versions = [
        register("m", stored_path=f, registry_db=db).version for _ in range(3)
    ]
    assert versions == [1, 2, 3]
    assert row_count(db) == 3


def test_check_exists_raises_when_path_missing(tmp_path):
    db = tmp_path / "registry.sqlite"
    missing = tmp_path / "nope.json"
    with pytest.raises(ModelPathNotFoundError):
        register("abc", stored_path=missing, registry_db=db)
    # check_exists=False succeeds without the file on disk
    rec = register("abc", stored_path=missing, registry_db=db, check_exists=False)
    assert rec.stored_path == os.path.abspath(str(missing))


def test_soft_delete_lifecycle(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    r1 = register("abc", stored_path=f, registry_db=db)  # v1
    delete("abc", registry_db=db)  # soft, all versions
    with pytest.raises(ModelNotFoundError):
        lookup("abc", registry_db=db)
    assert list_models(registry_db=db) == []
    hidden = list_models(include_deleted=True, registry_db=db)
    assert [h.version for h in hidden] == [1]
    assert hidden[0].deleted_at_utc is not None
    # Re-register with version=None auto-versions past the soft-deleted row
    register("abc", stored_path=f, registry_db=db)  # -> v2
    with pytest.raises(ModelNotFoundError):
        lookup("abc", version=1, registry_db=db)  # still soft-deleted
    assert lookup("abc", version=2, registry_db=db).deleted_at_utc is None
    assert row_count(db) == 2


def test_soft_delete_re_register_resurrects_same_version(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    r1 = register("abc", stored_path=f, registry_db=db)  # v1
    delete("abc", version=1, registry_db=db)
    with pytest.raises(ModelNotFoundError):
        lookup("abc", registry_db=db)
    # Re-register the same version -> row resurrected, created_at preserved
    r2 = register("abc", version=1, stored_path=f, registry_db=db)
    got = lookup("abc", registry_db=db)
    assert got.version == 1
    assert got.deleted_at_utc is None
    assert got.created_at_utc == r1.created_at_utc
    assert row_count(db) == 1


def test_re_delete_is_idempotent(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("abc", stored_path=f, registry_db=db)
    delete("abc", registry_db=db)
    first_stamp = list_models(include_deleted=True, registry_db=db)[0].deleted_at_utc
    delete("abc", registry_db=db)  # re-delete re-stamps, does not raise
    second_stamp = list_models(include_deleted=True, registry_db=db)[0].deleted_at_utc
    assert second_stamp is not None


def test_hard_delete(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("abc", stored_path=f, registry_db=db)
    delete("abc", soft=False, registry_db=db)
    with pytest.raises(ModelNotFoundError):
        lookup("abc", registry_db=db)
    assert row_count(db) == 0
    with pytest.raises(ModelNotFoundError):
        delete("abc", registry_db=db)  # nothing left to delete


def test_delete_version_scoped(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("m", version=1, stored_path=f, registry_db=db)
    register("m", version=2, stored_path=f, registry_db=db)
    delete("m", version=1, registry_db=db)
    with pytest.raises(ModelNotFoundError):
        lookup("m", version=1, registry_db=db)
    assert lookup("m", version=2, registry_db=db).version == 2
    with pytest.raises(ModelNotFoundError):
        delete("m", version=9, registry_db=db)


def test_lookup_miss(tmp_path):
    db = tmp_path / "registry.sqlite"
    with pytest.raises(ModelNotFoundError):
        lookup("ghost", registry_db=db)


def test_non_numeric_version_raises_valueerror(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db)
    with pytest.raises(ValueError):
        lookup("m", version="not-a-number", registry_db=db)


def test_lookup_path(tmp_path):
    db = tmp_path / "registry.sqlite"
    make_file(tmp_path, "rel.json")
    register("m", stored_path=tmp_path / "rel.json", registry_db=db,
             store_relative_to_db=True)
    assert lookup_path("m", registry_db=db) == "rel.json"
    assert lookup_path("m", version=1, registry_db=db) == "rel.json"


def test_next_version_includes_soft_deleted(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    assert next_version("m", registry_db=db) == 1
    register("m", stored_path=f, registry_db=db)
    assert next_version("m", registry_db=db) == 2
    delete("m", registry_db=db)
    assert next_version("m", registry_db=db) == 2  # includes soft-deleted
    register("m", version=2, stored_path=f, registry_db=db)
    assert next_version("m", registry_db=db) == 3


def test_list_models_ordering(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("b", stored_path=f, registry_db=db)
    register("a", version=1, stored_path=f, registry_db=db)
    register("a", version=2, stored_path=f, registry_db=db)
    assert [(r.model_id, r.version) for r in list_models(registry_db=db)] == [
        ("a", 1),
        ("a", 2),
        ("b", 1),
    ]


def test_metadata_roundtrip(tmp_path):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db,
             metadata={"tool": "save_system", "tags": ["x", "y"]})
    assert lookup("m", registry_db=db).metadata == {
        "tool": "save_system",
        "tags": ["x", "y"],
    }
    register("n", stored_path=f, registry_db=db)
    assert lookup("n", registry_db=db).metadata == {}


def test_make_model_id_deterministic():
    m1 = make_model_id("/some/path.json")
    m2 = make_model_id("/some/path.json")
    assert m1 == m2
    assert make_model_id("/other.json") != m1
    assert m1 == str(
        uuid.uuid5(uuid.NAMESPACE_URL, "dist-stack.models:/some/path.json")
    )
    # PathLike sources stringify the same way
    assert make_model_id(Path("/some/path.json")) == m1


def test_get_registry_path_precedence(tmp_path, monkeypatch):
    db1 = tmp_path / "a.sqlite"
    db2 = tmp_path / "b.sqlite"
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db1))
    assert get_registry_path() == str(db1)
    assert get_registry_path(registry_db=db2) == str(db2)
    assert get_registry_path(registry_db=str(db2)) == str(db2)
    monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB")
    with pytest.raises(RegistryUnavailableError):
        get_registry_path()


def test_ensure_schema_idempotent(tmp_path):
    db = tmp_path / "registry.sqlite"
    ensure_schema(db)
    ensure_schema(db)
    with sqlite3.connect(str(db)) as conn:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(models)")]
    assert user_version == 1
    assert cols == [
        "model_id",
        "version",
        "stored_path",
        "model_hash",
        "metadata",
        "created_at_utc",
        "deleted_at_utc",
    ]
