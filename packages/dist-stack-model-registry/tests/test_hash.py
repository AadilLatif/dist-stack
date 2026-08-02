"""Hash handling (§8 item 4)."""
from __future__ import annotations

import os

import pytest

from dist_stack import HashMismatchError, lookup, register


def make_file(tmp_path, name="model.json"):
    p = tmp_path / name
    p.write_text("{}")
    return p


def test_hash_stored_opaque(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db, model_hash="opaque-xyz")
    assert lookup("m", registry_db=db).model_hash == "opaque-xyz"


def test_lookup_expected_hash_match(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db, model_hash="h1")
    rec = lookup("m", registry_db=db, expected_hash="h1")
    assert rec.model_hash == "h1"


def test_lookup_expected_hash_mismatch(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db, model_hash="h1")
    with pytest.raises(HashMismatchError):
        lookup("m", registry_db=db, expected_hash="h2")


def test_lookup_expected_hash_mismatch_when_stored_none(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    register("m", stored_path=f, registry_db=db)
    with pytest.raises(HashMismatchError):
        lookup("m", registry_db=db, expected_hash="anything")


def test_hash_fn_invoked_with_stored_path(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    seen = []
    register("m", stored_path=f, registry_db=db,
             hash_fn=lambda p: seen.append(p) or f"h({p})")
    assert seen == [os.path.abspath(str(f))]
    rec = lookup("m", registry_db=db)
    assert rec.model_hash == f"h({os.path.abspath(str(f))})"


def test_hash_fn_uses_stored_relative_path_when_relative(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    seen = []
    register("m", stored_path=f, registry_db=db, store_relative_to_db=True,
             hash_fn=lambda p: seen.append(p) or f"h({p})")
    assert seen == ["model.json"]
    assert lookup("m", registry_db=db, resolve_path=False).model_hash == "h(model.json)"


def test_hash_fn_not_invoked_when_model_hash_given(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)

    def boom(p):
        raise AssertionError("hash_fn must not be called")

    register("m", stored_path=f, registry_db=db, model_hash="given", hash_fn=boom)
    assert lookup("m", registry_db=db).model_hash == "given"


def test_hash_optional(tmp_path):
    db = tmp_path / "r.sqlite"
    f = make_file(tmp_path)
    rec = register("m", stored_path=f, registry_db=db)
    assert rec.model_hash is None
    assert lookup("m", registry_db=db).model_hash is None
