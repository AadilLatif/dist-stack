"""Public surface of the provenance manifest module (`dist_stack.manifest`).

Re-exports the functional API (sidecar path derivation, write/read/check) and
the ``Manifest`` dataclass plus its constants. Nothing else is public.
"""

from __future__ import annotations

from .api import get_manifest_path, has_manifest, read_manifest, write_manifest
from .model import MANIFEST_SCHEMA_VERSION, MANIFEST_SUFFIX, Manifest

__all__ = [
    "Manifest",
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_SUFFIX",
    "get_manifest_path",
    "write_manifest",
    "read_manifest",
    "has_manifest",
]
