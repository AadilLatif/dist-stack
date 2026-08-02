"""Configuration loading for the workflow-runner.

Resolution order: explicit ``--config`` path > ``WORKFLOW_RUNNER_CONFIG`` env >
``./servers.yaml``. The loader validates:

- server **name uniqueness** (duplicate YAML keys rejected),
- **non-empty command** per server,
- **no NUL bytes** in command/args (the mcp SDK rejects embedded NULs in
  spawn parameters).

``runstore_db``, ``workflow_dir``, ``cwd`` and ``env`` values are
``~``-expanded so ``~/.cache/...`` paths survive the round trip.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .models import RunnerConfig, ServerSpec

DEFAULT_CONFIG_PATH = Path("servers.yaml")
ENV_CONFIG_VAR = "WORKFLOW_RUNNER_CONFIG"


class ConfigError(ValueError):
    """Raised when a servers.yaml config file is missing or invalid."""


try:  # PyYAML is the one third-party config dependency (spec §2.1)
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# Duplicate-key-safe YAML loader (name uniqueness validation)
# ---------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


if yaml is not None:  # pragma: no branch
    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_servers_config(path: str | os.PathLike | None = None) -> RunnerConfig:
    """Load and validate a ``servers.yaml`` config.

    When no config file exists at the resolved location an empty (server-less)
    ``RunnerConfig`` is returned so the runner boots cleanly without a config.
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return RunnerConfig()
    return _parse_config(_load_yaml(resolved), resolved)


def _resolve_path(path: str | os.PathLike | None) -> Path | None:
    if path is not None:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"config file not found: {candidate}")
        return candidate
    env_path = os.getenv(ENV_CONFIG_VAR)
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"{ENV_CONFIG_VAR} file not found: {candidate}")
        return candidate
    candidate = DEFAULT_CONFIG_PATH
    return candidate if candidate.is_file() else None


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to parse servers.yaml")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.load(raw, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")
    return data


def _parse_config(data: dict[str, Any], path: Path) -> RunnerConfig:
    runstore_db = _optional_str(data.get("runstore_db"), path)
    workflow_dir = _optional_str(data.get("workflow_dir"), path)

    servers_raw = data.get("servers", {})
    if servers_raw is None:
        servers_raw = {}
    if not isinstance(servers_raw, dict):
        raise ConfigError(f"config file {path}: 'servers' must be a mapping of name -> spec")

    specs = [_parse_server_spec(str(name), spec, path) for name, spec in servers_raw.items()]
    return RunnerConfig(runstore_db=runstore_db, workflow_dir=workflow_dir, servers=specs)


def _parse_server_spec(name: str, spec: Any, path: Path) -> ServerSpec:
    if not isinstance(spec, dict):
        raise ConfigError(f"config file {path}: server {name!r} spec must be a mapping")
    command = spec.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ConfigError(f"config file {path}: server {name!r}: 'command' must be a non-empty string")
    command = command.strip()

    args_raw = spec.get("args", [])
    if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
        raise ConfigError(f"config file {path}: server {name!r}: 'args' must be a list of strings")

    if "\x00" in command or any("\x00" in a for a in args_raw):
        raise ConfigError(
            f"config file {path}: server {name!r}: command/args must not contain NUL bytes"
        )

    env_raw = spec.get("env", {}) or {}
    if not isinstance(env_raw, dict):
        raise ConfigError(f"config file {path}: server {name!r}: 'env' must be a mapping")
    env = {str(k): _expand(str(v)) for k, v in env_raw.items()}

    cwd = _optional_str(spec.get("cwd"), path)
    timeout_raw = spec.get("timeout_s", 300)
    try:
        timeout_s = int(timeout_raw) if timeout_raw is not None else 300
    except (TypeError, ValueError):
        raise ConfigError(f"config file {path}: server {name!r}: 'timeout_s' must be an integer") from None
    if timeout_s <= 0:
        raise ConfigError(f"config file {path}: server {name!r}: 'timeout_s' must be a positive integer")

    return ServerSpec(
        name=name,
        command=command,
        args=list(args_raw),
        cwd=cwd,
        env=env,
        timeout_s=timeout_s,
    )


def _optional_str(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"config file {path}: expected a string value, got {type(value).__name__}")
    value = _expand(value)
    return value or None


def _expand(value: str) -> str:
    return str(Path(value).expanduser())
