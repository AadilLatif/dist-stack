"""Public surface of the model registry (`dist_stack.registry`).

Re-exports the functional API, the ``ModelRecord`` dataclass, and the
exception classes. Nothing else is public. MCP exposure is explicitly deferred
to Phase 2 (`dist_stack.mcp`).
"""

from __future__ import annotations

from .api import (
    delete,
    ensure_schema,
    get_registry_path,
    list_models,
    lookup,
    lookup_path,
    make_model_id,
    next_version,
    register,
    resolve_model_ref,
)
from .errors import (
    HashMismatchError,
    InvalidModelRefError,
    ModelNotFoundError,
    ModelPathNotFoundError,
    RegistryError,
    RegistryUnavailableError,
)
from .model import ModelRecord

__all__ = [
    "register",
    "lookup",
    "lookup_path",
    "delete",
    "list_models",
    "resolve_model_ref",
    "next_version",
    "make_model_id",
    "ensure_schema",
    "get_registry_path",
    "ModelRecord",
    "RegistryError",
    "InvalidModelRefError",
    "ModelNotFoundError",
    "ModelPathNotFoundError",
    "RegistryUnavailableError",
    "HashMismatchError",
]
