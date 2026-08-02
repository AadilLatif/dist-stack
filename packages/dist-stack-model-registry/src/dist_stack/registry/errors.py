"""Exception hierarchy for the model registry.

All registry errors subclass :class:`RegistryError`, which itself subclasses
:class:`ValueError`. This guarantees zero behavior change at every existing
catch site: all three legacy resolvers (grid-data-models, gdm-flow, erad)
raise plain ``ValueError`` with the exact messages pinned by the golden tests;
the distinct subclasses give new callers precise handling.
"""

from __future__ import annotations

__all__ = [
    "RegistryError",
    "InvalidModelRefError",
    "ModelNotFoundError",
    "ModelPathNotFoundError",
    "RegistryUnavailableError",
    "HashMismatchError",
]


class RegistryError(ValueError):
    """Base class for all model-registry errors."""


class InvalidModelRefError(RegistryError):
    """A model_ref payload carried neither a usable path nor a usable model_id."""


class ModelNotFoundError(RegistryError):
    """No registry row matched the requested model_id/version."""


class ModelPathNotFoundError(RegistryError):
    """The stored_path of a model does not exist on disk."""


class RegistryUnavailableError(RegistryError):
    """No registry DB path could be resolved (arg, model_ref, or env var)."""


class HashMismatchError(RegistryError):
    """The stored model_hash differs from the caller-supplied expected_hash."""
