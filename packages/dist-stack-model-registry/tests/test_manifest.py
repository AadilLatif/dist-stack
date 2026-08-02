"""Provenance manifest sidecar (v1): path derivation, write/read/has
round-trips, and JSON shape. See oracle doc 09 (§ manifest namespace).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from dist_stack.manifest import (
    MANIFEST_SCHEMA_VERSION,
    MANIFEST_SUFFIX,
    Manifest,
    get_manifest_path,
    has_manifest,
    read_manifest,
    write_manifest,
)


def test_get_manifest_path(tmp_path):
    artifact = tmp_path / "system.json"
    expected = tmp_path / "system.json.manifest.json"
    assert get_manifest_path(artifact) == expected
    assert get_manifest_path(str(artifact)) == expected
    assert str(get_manifest_path(artifact)) == f"{artifact}{MANIFEST_SUFFIX}"
    assert MANIFEST_SUFFIX == ".manifest.json"


def test_write_manifest_creates_file_with_correct_json(tmp_path):
    artifact = tmp_path / "system.json"
    write_manifest(
        artifact,
        artifact_type="gdm_system",
        tool="save_system",
        tool_version="1.2.3",
    )
    sidecar = get_manifest_path(artifact)
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["artifact_path"] == str(artifact)
    assert data["artifact_type"] == "gdm_system"
    assert data["tool"] == "save_system"
    assert data["tool_version"] == "1.2.3"
    assert data["model_id"] is None
    assert data["config"] == {}
    assert data["derived_from"] == []
    assert isinstance(data["created_at_utc"], str)


def test_read_manifest_roundtrip(tmp_path):
    artifact = tmp_path / "flow_run.json"
    original = write_manifest(
        artifact,
        artifact_type="gdm_flow_run",
        model_id="dist-stack.models:abc",
        model_version=3,
        model_hash="sha256:deadbeef",
        tool="run_flow",
        tool_version="0.9.0",
        package="dist-stack",
        package_version="1.0.0",
        config={"solver": "x", "tags": ["a", "b"]},
        derived_from=["/in/system.json"],
    )
    loaded = read_manifest(artifact)
    assert isinstance(loaded, Manifest)
    assert loaded == original
    assert loaded.artifact_type == "gdm_flow_run"
    assert loaded.model_id == "dist-stack.models:abc"
    assert loaded.model_version == 3
    assert loaded.model_hash == "sha256:deadbeef"
    assert loaded.config == {"solver": "x", "tags": ["a", "b"]}
    assert loaded.derived_from == ["/in/system.json"]


def test_has_manifest_true_false(tmp_path):
    artifact = tmp_path / "artifact.bin"
    assert not has_manifest(artifact)
    write_manifest(artifact, artifact_type="gdm_system", tool="t", tool_version="1")
    assert has_manifest(artifact)
    assert not has_manifest(tmp_path / "other.bin")
    assert not has_manifest(str(tmp_path / "other.bin"))


def test_write_manifest_all_optional_fields_none(tmp_path):
    artifact = tmp_path / "minimal.json"
    m = write_manifest(artifact, artifact_type="gdm_system", tool="t", tool_version="1")
    assert m.model_id is None
    assert m.model_version is None
    assert m.model_hash is None
    assert m.package is None
    assert m.package_version is None
    assert m.config == {}
    assert m.derived_from == []
    data = json.loads(get_manifest_path(artifact).read_text(encoding="utf-8"))
    for key in ("model_id", "model_version", "model_hash", "package", "package_version"):
        assert data[key] is None


def test_write_manifest_with_derived_from(tmp_path):
    artifact = tmp_path / "system.json"
    parents = ["/out/a.json", "/out/b.json"]
    write_manifest(
        artifact,
        artifact_type="gdm_system",
        tool="t",
        tool_version="1",
        derived_from=parents,
    )
    data = json.loads(get_manifest_path(artifact).read_text(encoding="utf-8"))
    assert data["derived_from"] == parents
    assert read_manifest(artifact).derived_from == parents


def test_read_manifest_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_manifest(tmp_path / "nope.json")
    assert not has_manifest(tmp_path / "nope.json")


def test_write_manifest_artifact_does_not_exist(tmp_path):
    artifact = tmp_path / "about_to_be_created" / "system.json"
    assert not artifact.exists()
    m = write_manifest(artifact, artifact_type="gdm_system", tool="t", tool_version="1")
    assert get_manifest_path(artifact).is_file()
    assert read_manifest(artifact) == m


def test_json_has_expected_fields_and_types(tmp_path):
    artifact = tmp_path / "system.json"
    write_manifest(
        artifact,
        artifact_type="gdm_system",
        model_id="m1",
        model_version=2,
        model_hash="h",
        tool="save_system",
        tool_version="1",
        package="pkg",
        package_version="0.1",
        config={"k": "v"},
        derived_from=["/a.json"],
    )
    data = json.loads(get_manifest_path(artifact).read_text(encoding="utf-8"))
    assert isinstance(data["schema_version"], int)
    assert isinstance(data["artifact_path"], str)
    assert isinstance(data["artifact_type"], str)
    assert isinstance(data["model_id"], str)
    assert isinstance(data["model_version"], int)
    assert isinstance(data["model_hash"], str)
    assert isinstance(data["tool"], str)
    assert isinstance(data["tool_version"], str)
    assert isinstance(data["package"], str)
    assert isinstance(data["package_version"], str)
    assert isinstance(data["config"], dict)
    assert isinstance(data["derived_from"], list)
    assert isinstance(data["created_at_utc"], str)
    assert set(data) == {
        "schema_version",
        "artifact_path",
        "artifact_type",
        "model_id",
        "model_version",
        "model_hash",
        "tool",
        "tool_version",
        "package",
        "package_version",
        "config",
        "derived_from",
        "created_at_utc",
    }


def test_schema_version_preserved_in_roundtrip(tmp_path):
    artifact = tmp_path / "system.json"
    write_manifest(artifact, artifact_type="gdm_system", tool="t", tool_version="1")
    loaded = read_manifest(artifact)
    assert loaded.schema_version == 1
    assert loaded.schema_version == MANIFEST_SCHEMA_VERSION
    text = get_manifest_path(artifact).read_text(encoding="utf-8")
    assert '"schema_version": 1' in text


def test_read_manifest_schema_mismatch_warns_but_reads(tmp_path):
    artifact = tmp_path / "system.json"
    write_manifest(
        artifact,
        artifact_type="gdm_system",
        tool="t",
        tool_version="1",
        schema_version=999,
    )
    with pytest.warns(UserWarning, match="schema_version"):
        loaded = read_manifest(artifact)
    assert loaded.schema_version == 999
    assert loaded.artifact_type == "gdm_system"


def test_write_manifest_accepts_str_and_pathlike(tmp_path):
    artifact = tmp_path / "system.json"
    write_manifest(str(artifact), artifact_type="gdm_system", tool="t", tool_version="1")
    assert has_manifest(artifact)
    write_manifest(artifact, artifact_type="gdm_system", tool="t", tool_version="1")
    assert has_manifest(str(artifact))


def test_write_manifest_non_serializable_config_defaults_to_str(tmp_path):
    artifact = tmp_path / "system.json"
    when = datetime.now(timezone.utc)
    write_manifest(
        artifact,
        artifact_type="gdm_system",
        tool="t",
        tool_version="1",
        config={"when": when},
    )
    data = json.loads(get_manifest_path(artifact).read_text(encoding="utf-8"))
    assert isinstance(data["config"]["when"], str)
    assert data["config"]["when"] == str(when)


def test_manifest_is_frozen(tmp_path):
    m = Manifest(
        artifact_path=str(tmp_path / "x.json"),
        artifact_type="gdm_system",
        tool="t",
        tool_version="1",
    )
    with pytest.raises(AttributeError):
        m.tool = "other"  # type: ignore[misc]


def test_manifest_created_at_utc_is_utc_iso(tmp_path):
    m = write_manifest(tmp_path / "x.json", artifact_type="gdm_system", tool="t", tool_version="1")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", m.created_at_utc)
    assert m.created_at_utc.endswith("+00:00")
