"""Golden-compat suite for `resolve_model_ref` (§8 item 3).

Pins the library byte-for-byte to the behavior of the three legacy
implementations (grid-data-models, gdm-flow, erad), which are byte-identical
except for erad wrapping the return in ``Path()``.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from dist_stack import (
    InvalidModelRefError,
    ModelNotFoundError,
    RegistryUnavailableError,
    register,
    resolve_model_ref,
)


# ---------------------------------------------------------------------------
# Reference: verbatim copy of the legacy gdm resolver
# (grid-data-models/src/gdm/mcp/server.py:75-124). The gdm-flow copy
# (gdm_flow/mcp/server.py:106-151) is byte-identical; erad
# (erad/mcp/simulation.py:22-67) differs only in wrapping the returned value
# in Path().
# ---------------------------------------------------------------------------
def _legacy_resolve(model_ref):
    for key in ("stored_path", "path", "source_path"):
        value = model_ref.get(key)
        if isinstance(value, str) and value.strip():
            return value

    model_id = model_ref.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_ref must include a path or model_id")

    version = model_ref.get("version")
    db_path = model_ref.get("registry_db") or os.getenv("DIST_STACK_MODEL_REGISTRY_DB")
    if not db_path:
        raise ValueError(
            "model_ref requires DIST_STACK_MODEL_REGISTRY_DB (or model_ref.registry_db) "
            "when path fields are not provided"
        )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if version is None:
            row = conn.execute(
                """
                SELECT stored_path FROM models
                WHERE model_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT stored_path FROM models
                WHERE model_id = ? AND version = ?
                LIMIT 1
                """,
                (model_id, int(version)),
            ).fetchone()

    if row is None:
        suffix = "latest" if version is None else f"version={version}"
        raise ValueError(f"model_ref not found for model_id={model_id}, {suffix}")

    return str(row["stored_path"])


def make_db(tmp_path, rows, name="ref.sqlite"):
    """Build a legacy 3-column registry DB with the given rows."""
    db = tmp_path / name
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE models (
                model_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                stored_path TEXT NOT NULL
            )
            """
        )
        for model_id, version, stored_path in rows:
            conn.execute(
                "INSERT INTO models (model_id, version, stored_path) VALUES (?, ?, ?)",
                (model_id, version, stored_path),
            )
    return db


def capture(fn, model_ref):
    """Return ("ok", value) or ("error", message); legacy messages are
    plain ValueError, ours are ValueError subclasses — compare by message."""
    try:
        return ("ok", fn(model_ref))
    except Exception as exc:  # noqa: BLE001
        return ("error", str(exc))


@pytest.mark.parametrize(
    "model_ref, rows, set_env",
    [
        # --- path-key passthrough (verbatim; no DB involved) ---
        ({"stored_path": "/tmp/verbatim.json"}, [], False),
        ({"path": "./a//b/../c.json"}, [], False),
        ({"source_path": "  keep/whitespace  "}, [], False),
        # first path key wins, in order
        (
            {"stored_path": "/s.json", "path": "/p.json", "source_path": "/c.json"},
            [],
            False,
        ),
        # Path object in a path key is ignored -> falls through to model_id
        ({"path": Path("/ignored.json"), "model_id": "m", "version": 1},
         [("m", 1, "/stored.json")], True),
        ({"stored_path": Path("/ignored.json"), "model_id": "m"},
         [("m", 1, "/stored.json")], True),
        # blank path values are ignored
        ({"path": "", "model_id": "m", "version": 1}, [("m", 1, "/stored.json")], True),
        ({"stored_path": "   ", "model_id": "m"}, [("m", 1, "/stored.json")], True),
        # --- model_id lookup: latest vs exact, int normalization ---
        ({"model_id": "m"}, [("m", 1, "/v1.json"), ("m", 3, "/v3.json")], True),
        ({"model_id": "m", "version": 1}, [("m", 1, "/v1.json"), ("m", 3, "/v3.json")], True),
        ({"model_id": "m", "version": "1"}, [("m", 1, "/v1.json"), ("m", 3, "/v3.json")], True),
        # --- misses ---
        ({"model_id": "ghost"}, [("m", 1, "/v1.json")], True),
        ({"model_id": "ghost", "version": 7}, [("m", 1, "/v1.json")], True),
        # raw-version interpolation in the miss message (int(2.0)==2 misses)
        ({"model_id": "ghost", "version": 2.0},
         [("m", 1, "/v1.json"), ("m", 3, "/v3.json")], True),
        # non-numeric version -> ValueError from int(), verbatim
        ({"model_id": "m", "version": "abc"}, [("m", 1, "/v1.json")], True),
        # --- payload errors ---
        ({}, [], False),
        ({"model_id": 123}, [], False),
        ({"model_id": "m"}, [], False),  # no DB -> RegistryUnavailableError
    ],
)
def test_differential_against_legacy(tmp_path, monkeypatch, model_ref, rows, set_env):
    db = make_db(tmp_path, rows)
    if set_env:
        monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    else:
        monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB", raising=False)
    assert capture(resolve_model_ref, model_ref) == capture(_legacy_resolve, model_ref)


def test_registry_db_override_beats_env(tmp_path, monkeypatch):
    db_env = make_db(tmp_path, [("m", 1, "/from_env.json")], name="env.sqlite")
    db_ref = make_db(tmp_path, [("m", 1, "/from_ref.json")], name="ref.sqlite")
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db_env))
    model_ref = {"model_id": "m", "version": 1, "registry_db": str(db_ref)}
    assert capture(_legacy_resolve, model_ref) == ("ok", "/from_ref.json")
    assert capture(resolve_model_ref, model_ref) == ("ok", "/from_ref.json")


def test_registry_db_override_beats_env_on_miss(tmp_path, monkeypatch):
    db_env = make_db(tmp_path, [("m", 1, "/from_env.json")], name="env.sqlite")
    db_ref = make_db(tmp_path, [("other", 1, "/x.json")], name="ref.sqlite")
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db_env))
    model_ref = {"model_id": "m", "version": 1, "registry_db": str(db_ref)}
    expected = ("error", "model_ref not found for model_id=m, version=1")
    assert capture(_legacy_resolve, model_ref) == expected
    assert capture(resolve_model_ref, model_ref) == expected


def test_env_fallback(tmp_path, monkeypatch):
    db = make_db(tmp_path, [("m", 3, "/env_v3.json")])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    assert resolve_model_ref({"model_id": "m"}) == "/env_v3.json"


def test_invalid_model_ref_message_verbatim():
    with pytest.raises(InvalidModelRefError) as ei:
        resolve_model_ref({})
    assert str(ei.value) == "model_ref must include a path or model_id"
    assert isinstance(ei.value, ValueError)
    with pytest.raises(InvalidModelRefError):
        resolve_model_ref({"model_id": 123})
    with pytest.raises(InvalidModelRefError):
        resolve_model_ref({"model_id": "   "})


def test_registry_unavailable_message_verbatim(monkeypatch):
    monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB", raising=False)
    with pytest.raises(RegistryUnavailableError) as ei:
        resolve_model_ref({"model_id": "m"})
    assert str(ei.value) == (
        "model_ref requires DIST_STACK_MODEL_REGISTRY_DB (or model_ref.registry_db) "
        "when path fields are not provided"
    )
    assert isinstance(ei.value, ValueError)


def test_miss_messages_verbatim(tmp_path, monkeypatch):
    db = make_db(tmp_path, [("m", 1, "/v1.json")])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    with pytest.raises(ModelNotFoundError) as ei:
        resolve_model_ref({"model_id": "ghost"})
    assert str(ei.value) == "model_ref not found for model_id=ghost, latest"
    with pytest.raises(ModelNotFoundError) as ei:
        resolve_model_ref({"model_id": "ghost", "version": 7})
    assert str(ei.value) == "model_ref not found for model_id=ghost, version=7"


def test_raw_version_interpolation_in_miss_message(tmp_path, monkeypatch):
    # The message interpolates the *raw* version value, not the int-cast.
    db = make_db(tmp_path, [("m", 1, "/v1.json"), ("m", 3, "/v3.json")])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    with pytest.raises(ModelNotFoundError) as ei:
        resolve_model_ref({"model_id": "m", "version": 2.0})
    assert str(ei.value) == "model_ref not found for model_id=m, version=2.0"


def test_non_numeric_version_raises_valueerror(tmp_path, monkeypatch):
    db = make_db(tmp_path, [("m", 1, "/v1.json")])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    with pytest.raises(ValueError) as ei:
        resolve_model_ref({"model_id": "m", "version": "abc"})
    assert str(ei.value) == "invalid literal for int() with base 10: 'abc'"
    # ...and the message matches the legacy int() failure byte-for-byte
    with pytest.raises(ValueError) as leg:
        _legacy_resolve({"model_id": "m", "version": "abc"})
    assert str(ei.value) == str(leg.value)


def test_path_object_falls_through_to_model_id(tmp_path, monkeypatch):
    db = make_db(tmp_path, [("m", 3, "/stored.json")])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    ref = {"path": Path("/ignored.json"), "model_id": "m", "version": 3}
    assert capture(_legacy_resolve, ref) == ("ok", "/stored.json")
    assert capture(resolve_model_ref, ref) == ("ok", "/stored.json")
    # A Path-only ref (no model_id) must raise the path-or-model_id error
    ref2 = {"path": Path("/ignored.json")}
    assert capture(resolve_model_ref, ref2) == capture(_legacy_resolve, ref2)
    assert capture(resolve_model_ref, ref2) == (
        "error",
        "model_ref must include a path or model_id",
    )


def test_returns_stored_path_verbatim(tmp_path, monkeypatch):
    # The resolver must return the stored path verbatim (resolve_path=False
    # internally): a DB-relative row comes back relative, never re-absoluted.
    db = tmp_path / "reg.sqlite"
    model_file = tmp_path / "model.json"
    model_file.write_text("{}")
    register("m", stored_path=model_file, registry_db=db, store_relative_to_db=True)
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    assert resolve_model_ref({"model_id": "m"}) == "model.json"


def test_passthrough_does_not_need_db(monkeypatch):
    monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB", raising=False)
    assert resolve_model_ref({"stored_path": "/a.json"}) == "/a.json"
    assert resolve_model_ref({"path": "/b.json"}) == "/b.json"
    assert resolve_model_ref({"source_path": "/c.json"}) == "/c.json"
