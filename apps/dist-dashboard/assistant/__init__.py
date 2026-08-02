"""LLM chat assistant for dist-dashboard (spec 15).

Pure-logic modules (``llm``, ``catalog``, ``policy``, ``router``, ``chat``,
``pool_runtime``, ``prompt``) contain no Streamlit imports so they are fully
unit-testable; ``view`` is the Streamlit render layer. The assistant is a
pure MCP client — it deliberately avoids ``data.py`` to exercise the MCP
surface end-to-end.
"""

from __future__ import annotations

from .catalog import build_catalog, demangle, mangle
from .chat import MAX_TOOL_RESULT_CHARS, MAX_TURNS, agent_turn
from .llm import (
    LLMClient,
    LLMConfig,
    LLMError,
    LLMResult,
    LLMToolCall,
    load_llm_config,
)
from .policy import (
    KNOWN_TOOLS,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    catalog_allowed,
    drift_report,
)
from .pool_runtime import PoolRuntime
from .prompt import SYSTEM_PROMPT
from .router import ToolRouter, TraceRecord

__all__ = [
    "SYSTEM_PROMPT",
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "LLMResult",
    "LLMToolCall",
    "load_llm_config",
    "build_catalog",
    "mangle",
    "demangle",
    "MAX_TURNS",
    "MAX_TOOL_RESULT_CHARS",
    "agent_turn",
    "catalog_allowed",
    "drift_report",
    "READ_ONLY_TOOLS",
    "WRITE_TOOLS",
    "KNOWN_TOOLS",
    "PoolRuntime",
    "ToolRouter",
    "TraceRecord",
]
