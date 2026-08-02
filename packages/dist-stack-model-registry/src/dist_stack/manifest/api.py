"""Public functional API of the provenance manifest sidecar.

Manifests are immutable (frozen) JSON sidecars written next to the artifact
they describe, at ``{artifact_path}{MANIFEST_SUFFIX}``. Stateless and
dependency-free: plain file I/O, no registry or env access.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import asdict
from pathlib import Path

from .model import MANIFEST_SCHEMA_VERSION, MANIFEST_SUFFIX, Manifest

__all__ = [
    "get_manifest_path",
    "write_manifest",
    "read_manifest",
    "has_manifest",
]


def get_manifest_path(artifact_path: str | os.PathLike) -> Path:
    """Return the expected manifest path: ``{artifact_path}{MANIFEST_SUFFIX}``.

    E.g. ``/path/to/system.json`` → ``/path/to/system.json.manifest.json``.
    """
    return Path(f"{os.fspath(artifact_path)}{MANIFEST_SUFFIX}")


def write_manifest(artifact_path: str | os.PathLike, **kwargs) -> Manifest:
    """Create a :class:`Manifest` from kwargs and write it as a JSON sidecar.

    The manifest is written next to ``artifact_path`` even when the artifact
    itself does not exist yet (it may be about to be created). The manifest's
    ``artifact_path`` defaults to ``str(artifact_path)`` unless overridden via
    kwargs. Serialization uses ``json.dumps`` with ``indent=2``,
    ``ensure_ascii=False``, and ``default=str`` for non-serializable values.
    Returns the frozen :class:`Manifest`.
    """
    kwargs.setdefault("artifact_path", os.fspath(artifact_path))
    manifest = Manifest(**kwargs)
    path = get_manifest_path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(manifest), indent=2, ensure_ascii=False, default=str)
    path.write_text(payload + "\n", encoding="utf-8")
    return manifest


def read_manifest(artifact_path: str | os.PathLike) -> Manifest:
    """Read and return the :class:`Manifest` from the sidecar file.

    Raises ``FileNotFoundError`` if no manifest exists. A ``schema_version``
    that does not match :data:`MANIFEST_SCHEMA_VERSION` is warned about but
    does not fail the read.
    """
    path = get_manifest_path(artifact_path)
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    version = data.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        warnings.warn(
            f"manifest schema_version={version!r} does not match expected "
            f"{MANIFEST_SCHEMA_VERSION}: {path}",
            stacklevel=2,
        )
    return Manifest(**data)


def has_manifest(artifact_path: str | os.PathLike) -> bool:
    """Check whether a manifest sidecar exists next to ``artifact_path``."""
    return get_manifest_path(artifact_path).is_file()
