"""Exception hierarchy for the knowledge graph.

All KG errors subclass :class:`KGError`, which itself subclasses
:class:`ValueError`. This mirrors the runstore/registry convention, so best-effort
wrappers can catch the base class while the distinct subclasses give new callers
precise handling.
"""

from __future__ import annotations

__all__ = [
    "KGError",
    "KGUnavailableError",
    "NodeNotFoundError",
]


class KGError(ValueError):
    """Base class for all knowledge-graph errors."""


class KGUnavailableError(KGError):
    """No KG DB path could be resolved (arg or env var)."""


class NodeNotFoundError(KGError):
    """No KG node matched the requested node_id (or a required edge endpoint)."""
