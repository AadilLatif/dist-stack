"""Env var read lazily per call, never at import (§8 item 6).

Mirrors grid-data-models/tests/test_mcp_server.py:100-106 (and the gdm-flow /
erad copies): ``DIST_STACK_MODEL_REGISTRY_DB`` is set *after* the library has
already been imported and must be honored on the next call.
"""
from __future__ import annotations

import pytest

import dist_stack  # noqa: F401  (imported BEFORE env is set — the point)
from dist_stack import (
    ModelNotFoundError,
    RegistryUnavailableError,
    get_registry_path,
    lookup,
    register,
    resolve_model_ref,
)


def make_file(tmp_path, name="model.json"):
    p = tmp_path / name
    p.write_text("{}")
    return p


def test_env_set_after_import_resolves(tmp_path, monkeypatch):
    db = tmp_path / "registry.sqlite"
    f = make_file(tmp_path)
    # Library was imported at module top; env var is set only now.
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    register("m", stored_path=f)  # no registry_db arg -> env var
    rec = lookup("m")  # no registry_db arg -> env var
    assert rec.model_id == "m"
    assert rec.version == 1


def test_env_unset_raises_registry_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB", raising=False)
    with pytest.raises(RegistryUnavailableError):
        get_registry_path()
    with pytest.raises(RegistryUnavailableError):
        lookup("m")
    with pytest.raises(RegistryUnavailableError):
        resolve_model_ref({"model_id": "m"})


def test_env_read_lazily_per_call(tmp_path, monkeypatch):
    f = make_file(tmp_path)
    db1 = tmp_path / "one.sqlite"
    db2 = tmp_path / "two.sqlite"
    register("m", stored_path=f, registry_db=db1)
    register("n", stored_path=f, registry_db=db2)

    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db1))
    assert lookup("m").model_id == "m"
    with pytest.raises(ModelNotFoundError):
        lookup("n")

    # Switching the env var between calls must take effect immediately.
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db2))
    assert lookup("n").model_id == "n"
    with pytest.raises(ModelNotFoundError):
        lookup("m")

    monkeypatch.delenv("DIST_STACK_MODEL_REGISTRY_DB")
    with pytest.raises(RegistryUnavailableError):
        lookup("n")


def test_empty_env_var_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", "")
    with pytest.raises(RegistryUnavailableError):
        get_registry_path()
