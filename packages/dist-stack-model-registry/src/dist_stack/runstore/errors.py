"""Exception hierarchy for the runstore.

All runstore errors subclass :class:`RunstoreError`, which itself subclasses
:class:`ValueError`. This guarantees zero behavior change at existing catch
sites (the domain mirrors wrap best-effort ``create_run``/``attach_artifact``
calls in ``try/except RunstoreUnavailableError: logger.warning(...)``), while
the distinct subclasses give new callers precise handling.
"""

from __future__ import annotations

__all__ = [
    "RunstoreError",
    "RunstoreUnavailableError",
    "RunNotFoundError",
    "RunExistsError",
    "ArtifactPathNotFoundError",
]


class RunstoreError(ValueError):
    """Base class for all runstore errors."""


class RunstoreUnavailableError(RunstoreError):
    """No runstore DB path could be resolved (arg or env var)."""


class RunNotFoundError(RunstoreError):
    """No runstore row matched the requested run_id."""


class RunExistsError(RunstoreError):
    """A run with the given run_id already exists (runs are NOT upserts)."""


class ArtifactPathNotFoundError(RunstoreError):
    """The artifact file to attach does not exist on disk."""
