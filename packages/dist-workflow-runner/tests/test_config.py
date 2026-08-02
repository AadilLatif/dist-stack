"""Config parsing tests: valid, missing command, duplicate names, NUL bytes."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_runner.config import ConfigError, load_servers_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_config(tmp_path):
    path = _write(
        tmp_path,
        """
runstore_db: ~/.cache/dist-stack/runstore.db
workflow_dir: ./workflows
servers:
  gdm:
    command: gdm-mcp
    env:
      DIST_STACK_MODEL_REGISTRY_DB: ~/.cache/dist-stack/registry.db
  gdm_flow:
    command: python
    args: ["-m", "gdm_flow.mcp"]
    cwd: ~/repos/gdm-flow
  shift:
    command: python
    args: ["-m", "shift.mcp_server"]
    timeout_s: 120
""",
    )
    cfg = load_servers_config(path)
    assert cfg.runstore_db == str(Path("~/.cache/dist-stack/runstore.db").expanduser())
    assert cfg.workflow_dir == "workflows"  # normalized (./ dropped by Path)
    assert [s.name for s in cfg.servers] == ["gdm", "gdm_flow", "shift"]

    gdm = cfg.servers[0]
    assert gdm.command == "gdm-mcp"
    assert gdm.env == {
        "DIST_STACK_MODEL_REGISTRY_DB": str(Path("~/.cache/dist-stack/registry.db").expanduser())
    }
    assert gdm.args == []
    assert gdm.timeout_s == 300

    gdm_flow = cfg.servers[1]
    assert gdm_flow.command == "python"
    assert gdm_flow.args == ["-m", "gdm_flow.mcp"]
    assert gdm_flow.cwd == str(Path("~/repos/gdm-flow").expanduser())

    assert cfg.servers[2].timeout_s == 120


def test_missing_command(tmp_path):
    path = _write(
        tmp_path,
        """
servers:
  gdm: { args: ["-m", "gdm.mcp"] }
""",
    )
    with pytest.raises(ConfigError, match="command"):
        load_servers_config(path)


def test_duplicate_names(tmp_path):
    path = _write(
        tmp_path,
        """
servers:
  gdm: { command: gdm-mcp }
  gdm: { command: other-mcp }
""",
    )
    with pytest.raises(ConfigError, match="duplicate key"):
        load_servers_config(path)


def test_nul_bytes(tmp_path):
    path = _write(
        tmp_path,
        """
servers:
  gdm: { command: "gdm-mcp\\x00evil" }
""",
    )
    with pytest.raises(ConfigError, match="NUL"):
        load_servers_config(path)


def test_nul_bytes_in_args(tmp_path):
    path = _write(
        tmp_path,
        """
servers:
  gdm: { command: python, args: ["-m", "evil\\x00module"] }
""",
    )
    with pytest.raises(ConfigError, match="NUL"):
        load_servers_config(path)


def test_default_config_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_servers_config()
    assert cfg.servers == []
    assert cfg.runstore_db is None


def test_missing_explicit_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_servers_config(tmp_path / "does-not-exist.yaml")


def test_env_config_resolution(tmp_path, monkeypatch):
    path = _write(tmp_path, "runstore_db: /tmp/x.db\nservers:\n  a: { command: a }\n")
    monkeypatch.setenv("WORKFLOW_RUNNER_CONFIG", str(path))
    cfg = load_servers_config()
    assert cfg.runstore_db == "/tmp/x.db"
    assert [s.name for s in cfg.servers] == ["a"]
