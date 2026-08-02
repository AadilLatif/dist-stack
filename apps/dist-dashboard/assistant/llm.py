"""LLM provider layer: a thin ``AsyncOpenAI`` wrapper (spec 15 §A).

Native function-tool calling through the OpenAI chat-completions protocol
(``tools``/``tool_calls``) — NOT a "call this JSON" convention. Each tool call
carries a per-call ``id`` that the caller must echo back in the matching
``role: "tool"`` message.

No Streamlit imports here: this module is pure async and unit-testable.
Endpoints without tool support simply return text, and the agent loop ends
after turn 1 (the caller treats an empty ``tool_calls`` tuple as "answer").
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class LLMError(RuntimeError):
    """Raised when a chat-completions request fails."""


@dataclass(frozen=True)
class LLMConfig:
    """Resolved endpoint + model configuration."""

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 120.0
    max_tokens: int = 2048
    configured: bool = False

    @property
    def local(self) -> bool:
        """True for local/Ollama-style endpoints that need no real key."""
        return "localhost" in self.base_url or "127.0.0.1" in self.base_url


@dataclass(frozen=True)
class LLMToolCall:
    """One function-tool call requested by the model."""

    id: str
    name: str  # mangled "server__tool"
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResult:
    """One assistant turn from the model: text, tool calls, or both."""

    text: str  # "" when only tool_calls were requested
    tool_calls: tuple[LLMToolCall, ...]


class LLMClient:
    """Async OpenAI client with a small, purpose-built surface."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        # The OpenAI SDK rejects an empty api_key; local endpoints (Ollama,
        # vLLM) don't need one, so substitute a placeholder.
        api_key = config.api_key or ("local" if config.local else "")
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=api_key,
            timeout=config.timeout_s,
            max_retries=0,  # the agent loop is the retry layer
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> LLMResult:
        """Run one chat-completions call and normalise the response.

        ``tools`` is the OpenAI tool-catalog (``{"type": "function", ...}``);
        an empty list omits the ``tools`` field so tool-less endpoints degrade
        gracefully. Errors are raised as :class:`LLMError`.
        """
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — normalise every SDK failure
            raise LLMError(f"chat-completions request failed: {exc}") from exc

        try:
            message = resp.choices[0].message
        except (AttributeError, IndexError) as exc:  # pragma: no cover
            raise LLMError("chat-completions response had no choices") from exc

        tool_calls: list[LLMToolCall] = []
        for tc in message.tool_calls or []:
            if getattr(tc, "type", "function") != "function":
                continue
            raw = getattr(getattr(tc, "function", None), "arguments", None) or "{}"
            try:
                arguments = json.loads(raw)
            except (TypeError, ValueError):
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                LLMToolCall(
                    id=tc.id or "",
                    name=getattr(tc.function, "name", "") or "",
                    arguments=arguments,
                )
            )
        return LLMResult(text=message.content or "", tool_calls=tuple(tool_calls))


def load_llm_config(secrets: dict | None = None, ui: dict | None = None) -> LLMConfig:
    """Resolve the LLM configuration.

    Resolution order: the ``ui`` override (sidebar fields, e.g.
    ``{"base_url":…, "api_key":…, "model":…}``) wins, then env vars
    (``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL``), then
    ``secrets["llm"]`` (e.g. ``st.secrets``), then built-in defaults for
    ``base_url``/``model``. ``api_key`` may stay empty — callers treat that as
    "LLM not configured" unless the endpoint is local (Ollama etc.), where any
    placeholder key works.
    """
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if secrets is not None:
        llm = {}
        try:
            raw = getattr(secrets, "get", None)
            if callable(raw):
                nested = raw("llm", {}) or {}
                if isinstance(nested, dict):
                    llm = nested
        except Exception:  # pragma: no cover — malformed secrets must not crash
            llm = {}
        base_url = base_url or llm.get("base_url")
        api_key = api_key or llm.get("api_key")
        model = model or llm.get("model")

    if ui is not None:
        base_url = (ui.get("base_url") or "").strip() or base_url
        api_key = (ui.get("api_key") or "").strip() or api_key
        model = (ui.get("model") or "").strip() or model

    base_url = base_url or DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    api_key = (api_key or "").strip()
    configured = bool(api_key) or "localhost" in base_url or "127.0.0.1" in base_url
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        configured=configured,
    )
