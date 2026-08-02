"""Streamlit render layer for the assistant page (spec 15 §D).

Owns the session-state wiring, the chat transcript, the tool-call trace
expanders, the sidebar controls and the agent-turn runner. Everything that
talks to the LLM or the MCP pool lives behind :class:`assistant.PoolRuntime`
on its own thread; this module only does Streamlit.

Session state keys (spec 15 §C): ``assistant_messages`` (OpenAI wire format),
``assistant_traces`` (per-user-message list of per-round trace lists),
``assistant_catalog`` / ``assistant_catalog_key`` (cached tool array + its
(servers.yaml, allow_write) hash), ``assistant_runtime`` /
``assistant_servers_yaml`` (the live PoolRuntime + its config path),
``assistant_allow_write`` (default False), ``assistant_server_status``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import threading
from functools import partial
from pathlib import Path
from typing import Any

import streamlit as st

import styles
from workflow_runner.config import load_servers_config

from . import build_catalog, load_llm_config
from .chat import MAX_TURNS, agent_turn
from .llm import LLMClient
from .pool_runtime import PoolRuntime
from .prompt import SYSTEM_PROMPT
from .router import ToolRouter, TraceRecord, truncate_text

SUGGESTED_PROMPTS = [
    "Show me the provenance chain for run wf_abc",
    "Which runs failed recently, and why?",
    "Summarise the knowledge graph: node and edge counts by type",
]

DOT_CLASS = {"succeeded": "dot--ok", "failed": "dot--error", "blocked": "dot--missing"}


def _default_servers_yaml() -> str | None:
    """The app's own servers.yaml (env override handled by the caller)."""
    p = Path(__file__).resolve().parent.parent / "servers.yaml"
    return str(p) if p.is_file() else None


def _secrets() -> dict | None:
    try:
        return st.secrets  # type: ignore[return-value]
    except Exception:  # pragma: no cover — no secrets.toml is a valid state
        return None


def _init_state() -> None:
    if "assistant_messages" not in st.session_state:
        st.session_state["assistant_messages"] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
    if "assistant_traces" not in st.session_state:
        st.session_state["assistant_traces"] = []
    if "assistant_allow_write" not in st.session_state:
        st.session_state["assistant_allow_write"] = False
    if "assistant_running" not in st.session_state:
        st.session_state["assistant_running"] = False
    if "assistant_llm_base_url" not in st.session_state:
        st.session_state["assistant_llm_base_url"] = ""
    if "assistant_llm_api_key" not in st.session_state:
        st.session_state["assistant_llm_api_key"] = ""
    if "assistant_llm_model" not in st.session_state:
        st.session_state["assistant_llm_model"] = ""


def _ui_llm_config() -> dict:
    """Sidebar LLM overrides (empty strings mean "no UI override")."""
    return {
        "base_url": st.session_state.get("assistant_llm_base_url", ""),
        "api_key": st.session_state.get("assistant_llm_api_key", ""),
        "model": st.session_state.get("assistant_llm_model", ""),
    }


def _cap_history(messages: list[dict[str, Any]]) -> None:
    """Keep the last ``DIST_DASHBOARD_MAX_HISTORY`` non-system messages."""
    limit = int(os.environ.get("DIST_DASHBOARD_MAX_HISTORY", "40"))
    if len(messages) <= 1:  # just the system prompt
        return
    head = messages[:1] if messages and messages[0]["role"] == "system" else []
    tail = messages[1:] if head else messages
    if len(tail) > limit:
        st.session_state["assistant_messages"] = head + tail[-limit:]


# ---------------------------------------------------------------------------
# Runtime / catalog wiring
# ---------------------------------------------------------------------------


def _resolve_servers_yaml() -> str | None:
    override = (
        st.session_state.get("assistant_servers_yaml_input")
        or os.environ.get("DIST_DASHBOARD_SERVERS_YAML")
        or ""
    )
    if override:
        candidate = Path(override).expanduser()
        return str(candidate) if candidate.is_file() else None
    return _default_servers_yaml()


def _get_runtime() -> tuple[PoolRuntime | None, str | None]:
    path = _resolve_servers_yaml()
    stored = st.session_state.get("assistant_servers_yaml")
    runtime: PoolRuntime | None = st.session_state.get("assistant_runtime")
    if runtime is not None and stored == path:
        return runtime, path
    # path changed (or the runtime was requested for a restart) — tear down
    if runtime is not None:
        try:
            runtime.stop()
        except Exception:  # pragma: no cover
            pass
        st.session_state["assistant_runtime"] = None
    if path is None:
        st.session_state["assistant_servers_yaml"] = None
        st.session_state["assistant_server_status"] = {}
        return None, None
    try:
        config = load_servers_config(path)
    except Exception as exc:
        st.session_state["assistant_server_status"] = {}
        st.warning(f"Could not load servers.yaml: {exc}")
        return None, path
    runtime = PoolRuntime(config.servers)
    try:
        runtime.start()
    except Exception as exc:  # pragma: no cover
        st.error(f"Could not start the MCP pool: {exc}")
        runtime = None
    st.session_state["assistant_runtime"] = runtime
    st.session_state["assistant_servers_yaml"] = path
    if runtime is not None:
        st.session_state["assistant_server_status"] = runtime.statuses()
    return runtime, path


def _catalog_key(path: str | None, allow_write: bool) -> str:
    return hashlib.sha256(f"{path}|{allow_write}".encode()).hexdigest()


def _get_catalog(runtime: PoolRuntime | None, allow_write: bool) -> list[dict[str, Any]]:
    if runtime is None:
        return []
    path = st.session_state.get("assistant_servers_yaml") or ""
    key = _catalog_key(path, allow_write)
    if st.session_state.get("assistant_catalog_key") != key:
        try:
            catalog = runtime.call(
                partial(build_catalog, runtime, runtime.names, allow_write=allow_write)
            )
        except Exception as exc:
            st.warning(f"Could not build the tool catalog: {exc}")
            catalog = []
        st.session_state["assistant_catalog"] = catalog
        st.session_state["assistant_catalog_key"] = key
        st.session_state["assistant_server_status"] = runtime.statuses()
    return st.session_state.get("assistant_catalog") or []


# ---------------------------------------------------------------------------
# Transcript + trace rendering
# ---------------------------------------------------------------------------


def _json_block(value: Any, limit: int = 2000) -> None:
    """Render a tool payload safely (st.json when intact, st.code when cut).

    Tool results are data, never markdown — embedded instructions cannot
    execute as UI.
    """
    if value is None:
        st.caption("—")
        return
    text = json.dumps(value, indent=2, default=str)
    if len(text) <= limit:
        try:
            st.json(json.loads(text), expanded=False)
        except ValueError:  # pragma: no cover
            st.code(text, language="json")
    else:
        st.code(truncate_text(text, limit), language="json")


def _render_trace_expander(trace: list[TraceRecord]) -> None:
    with st.expander(f"Tool calls · {len(trace)}"):
        for record in trace:
            dot = DOT_CLASS.get(record.status, "dot--missing")
            row = (
                f'<div class="trace-row">'
                f'<span class="dot {dot}"></span>'
                f'<span class="server">{record.server}</span>'
                f'<span class="tool">{record.tool}</span>'
                f'{styles.badge(record.status, record.status)}'
                f'<span class="duration">{record.duration_ms} ms</span>'
                f"</div>"
            )
            st.markdown(row, unsafe_allow_html=True)
            left, right = st.columns(2)
            with left:
                st.markdown("**args**")
                _json_block(record.arguments)
            with right:
                if record.status == "succeeded":
                    st.markdown("**result**")
                    _json_block(record.result)
                else:
                    st.markdown("**error**")
                    _json_block(record.error)
            if record.status == "failed" and record.error:
                st.caption(record.error)
            if record.status == "blocked" and record.error:
                st.caption(record.error)


def _render_transcript() -> None:
    messages = st.session_state.get("assistant_messages", [])
    traces = st.session_state.get("assistant_traces", [])
    trace_idx = 0
    current_rounds: list[list[TraceRecord]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "user":
            current_rounds = traces[trace_idx] if trace_idx < len(traces) else []
            trace_idx += 1
            with st.chat_message("user"):
                st.markdown(msg.get("content") or "")
        elif role == "assistant":
            with st.chat_message("assistant"):
                content = msg.get("content")
                if content:
                    st.markdown(content)
                if msg.get("tool_calls"):
                    for round_trace in current_rounds or []:
                        if round_trace:
                            _render_trace_expander(round_trace)
        # "tool" messages are surfaced via the trace expanders only.


def _submit_prompt(prompt: str) -> None:
    messages = st.session_state.get("assistant_messages")
    if messages is None:
        _init_state()
        messages = st.session_state["assistant_messages"]
    messages.append({"role": "user", "content": prompt})
    st.session_state["assistant_traces"].append([])


# ---------------------------------------------------------------------------
# Agent-turn runner (progressive UI)
# ---------------------------------------------------------------------------


def _run_turn(runtime: PoolRuntime, llm_cfg: Any, allow_write: bool) -> None:
    st.session_state["assistant_running"] = True
    max_turns = int(os.environ.get("DIST_DASHBOARD_MAX_TURNS", str(MAX_TURNS)))
    messages = list(st.session_state.get("assistant_messages", []))
    catalog = st.session_state.get("assistant_catalog") or []
    router = ToolRouter(runtime)
    llm = LLMClient(llm_cfg)

    work_messages = [dict(m) for m in messages]
    stopped_box: list[bool] = [False]
    q: "queue.Queue[tuple]" = queue.Queue()

    def worker() -> None:
        async def _consume() -> None:
            gen = agent_turn(
                work_messages,
                catalog,
                router,
                llm,
                max_turns=max_turns,
                allow_write=allow_write,
                stopped=stopped_box,
            )
            try:
                while True:
                    msg, trace = await gen.__anext__()
                    q.put(("round", msg, trace))
            except StopAsyncIteration:
                pass
            except Exception as exc:  # pragma: no cover
                q.put(("error", exc))

        try:
            asyncio.run(_consume())
        except Exception as exc:  # pragma: no cover
            q.put(("error", exc))

    worker_thread = threading.Thread(target=worker, daemon=True, name="assistant-agent")
    worker_thread.start()

    status = st.status("Thinking…", expanded=False)
    rounds_box = st.container()
    rounds: list[list[TraceRecord]] = []
    errored: Exception | None = None
    kind = None
    while True:
        try:
            kind, payload = q.get(timeout=0.2)
        except queue.Empty:
            if not worker_thread.is_alive():
                break
            continue
        if kind == "round":
            _msg, trace = payload
            rounds.append(trace)
            names = ", ".join(r.tool for r in trace) or "tools"
            status.update(label=f"Tool round · {names}")
            with rounds_box:
                _render_trace_expander(trace)
        elif kind == "done":
            break
        elif kind == "error":
            errored = payload
            break

    stopped = bool(stopped_box[0])
    st.session_state["assistant_messages"] = work_messages
    st.session_state["assistant_traces"][-1] = rounds
    if errored is not None:
        status.update(label="Assistant error", state="error")
        st.error(f"Agent loop failed: {errored}")
    elif stopped:
        note = f"Stopped after {max_turns} tool rounds."
        st.session_state["assistant_messages"].append({"role": "assistant", "content": note})
        status.update(label=note, state="error")
    else:
        status.update(label="Done", state="complete")
    _cap_history(st.session_state["assistant_messages"])
    st.session_state["assistant_running"] = False
    st.rerun()


# ---------------------------------------------------------------------------
# Sidebar section (called from app.py's render_sidebar)
# ---------------------------------------------------------------------------


def _chip(name: str, status: str) -> str:
    cls = {"connected": "dot--ok", "error": "dot--error"}.get(status, "dot--missing")
    label = {"connected": "connected", "error": "error", "configured": "configured"}.get(
        status, status
    )
    return (
        f'<div class="chip"><span class="dot {cls}"></span>'
        f"<span>{name}</span><span class=\"chip-status\">{label}</span></div>"
    )


def _test_connections() -> None:
    runtime: PoolRuntime | None = st.session_state.get("assistant_runtime")
    if runtime is None:
        st.session_state["assistant_server_status"] = {}
    else:
        try:
            st.session_state["assistant_server_status"] = runtime.check_all(per_server_timeout_s=20)
        except Exception as exc:  # pragma: no cover
            st.session_state["assistant_server_status"] = {n: "error" for n in runtime.names}
            st.sidebar.error(f"Test connections failed: {exc}")
    # No st.rerun(): Streamlit already reruns the script after a callback.


def _restart_runtime() -> None:
    runtime: PoolRuntime | None = st.session_state.get("assistant_runtime")
    if runtime is not None:
        try:
            runtime.stop()
        except Exception:  # pragma: no cover
            pass
    for key in (
        "assistant_runtime",
        "assistant_catalog",
        "assistant_catalog_key",
        "assistant_server_status",
        "assistant_servers_yaml",
    ):
        st.session_state.pop(key, None)
    # No st.rerun(): Streamlit already reruns the script after a callback.


def render_sidebar_section() -> None:
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Assistant**")
    st.sidebar.caption(
        "Natural language over the MCP servers. Read-only unless write tools "
        "are enabled."
    )
    st.sidebar.text_input(
        "servers.yaml",
        key="assistant_servers_yaml_input",
        value=st.session_state.get("assistant_servers_yaml_input", ""),
        placeholder=_default_servers_yaml() or "…/servers.yaml",
        help="Path to the MCP server config. Falls back to "
        "DIST_DASHBOARD_SERVERS_YAML, then <app dir>/servers.yaml.",
    )

    statuses = st.session_state.get("assistant_server_status") or {}
    if statuses:
        st.sidebar.markdown("**Server status**")
        chips = "".join(_chip(name, state) for name, state in sorted(statuses.items()))
        st.sidebar.markdown(chips, unsafe_allow_html=True)
        st.sidebar.caption("Test connections to refresh.")

    left, right = st.sidebar.columns(2)
    with left:
        st.button("Test connections", key="assistant_test_conn", on_click=_test_connections)
    with right:
        st.button("Restart connections", key="assistant_restart_conn", on_click=_restart_runtime)

    write_enabled = st.sidebar.toggle(
        "Enable write tools",
        key="assistant_allow_write",
        value=False,
        help="DANGEROUS: lets the assistant run simulations, execute workflows, "
        "ingest data and export artifacts.",
    )
    if write_enabled:
        st.sidebar.warning("Write tools enabled — local admin power.")

    try:
        cfg = load_llm_config(_secrets(), _ui_llm_config())
        st.sidebar.markdown("**LLM**")
        st.sidebar.text_input(
            "Base URL",
            key="assistant_llm_base_url",
            placeholder="https://api.openai.com/v1 · Ollama: http://localhost:11434/v1",
            help="OpenAI-compatible endpoint. Set in the UI, or via "
            "LLM_BASE_URL / the [llm] secrets block (UI wins).",
        )
        st.sidebar.text_input(
            "API key",
            key="assistant_llm_api_key",
            type="password",
            placeholder="sk-… · Ollama: any value",
            help="UI value wins over LLM_API_KEY / the [llm] secrets block. "
            "Ollama and other local endpoints accept any placeholder.",
        )
        st.sidebar.text_input(
            "Model",
            key="assistant_llm_model",
            placeholder="gpt-4o-mini · qwen3.6:35b",
            help="UI value wins over LLM_MODEL / the [llm] secrets block.",
        )
        if cfg.configured:
            st.sidebar.caption(f"Model: {cfg.model}")
        else:
            st.sidebar.caption(
                "LLM not configured — set the fields above or the "
                "[llm] secrets block."
            )
    except Exception:  # pragma: no cover
        st.sidebar.caption("LLM not configured")

    if st.sidebar.button("Clear conversation", key="assistant_clear_conv"):
        st.session_state["assistant_messages"] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        st.session_state["assistant_traces"] = []
        st.session_state.pop("assistant_catalog", None)
        st.session_state.pop("assistant_catalog_key", None)
        st.rerun()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def render_chat_view(_cfg: Any = None) -> None:
    _init_state()
    allow_write = bool(st.session_state.get("assistant_allow_write", False))

    st.title("Assistant")
    st.caption(
        "Assistant — natural language over the MCP servers. Read-only unless "
        "write tools are enabled."
    )
    if allow_write:
        st.warning(
            "Write tools are enabled: the assistant can run simulations, "
            "execute workflows, ingest graph data and export artifacts. Treat "
            "this as local admin power."
        )

    llm_cfg = load_llm_config(_secrets(), _ui_llm_config())
    runtime, path = _get_runtime()
    if runtime is not None:
        st.session_state["assistant_server_status"] = runtime.statuses()

    fresh = len(
        [m for m in st.session_state.get("assistant_messages", []) if m["role"] != "system"]
    ) == 0

    if not llm_cfg.configured:
        styles.empty_state(
            "LLM not configured",
            "Set the <b>LLM</b> fields in the sidebar, or the "
            "<code>LLM_API_KEY</code> env var / <code>[llm]</code> block in "
            "<code>.streamlit/secrets.toml</code>. For a local model "
            "(Ollama, vLLM), point <b>Base URL</b> at the local endpoint "
            "(e.g. <code>http://localhost:11434/v1</code>), put any value in "
            "<b>API key</b>, and set <b>Model</b> (e.g. "
            "<code>qwen3.6:35b</code>).",
        )
        _render_transcript()
        return

    if path is None:
        styles.empty_state(
            "No servers.yaml found",
            "The assistant needs an MCP server config to build its tool "
            "catalog. Create <code>apps/dist-dashboard/servers.yaml</code> "
            "(see the README) or point the sidebar field / "
            "<code>DIST_DASHBOARD_SERVERS_YAML</code> at one.",
        )
        _render_transcript()
        st.chat_input("Ask about runs, the graph, or run a workflow…", disabled=True)
        return

    catalog = _get_catalog(runtime, allow_write)
    if not catalog:
        styles.empty_state(
            "No tools available",
            "No MCP server answered the tool-catalog probe. Check the sidebar "
            "server status, then hit <b>Test connections</b>.",
        )
    elif fresh:
        styles.empty_state(
            "Ask the assistant",
            "Natural language over the MCP servers. Try one of these, or ask "
            "your own question:",
        )
        cols = st.columns(len(SUGGESTED_PROMPTS))
        for col, prompt in zip(cols, SUGGESTED_PROMPTS):
            with col:
                if st.button(prompt, key=f"sugg_{prompt[:12]}"):
                    _submit_prompt(prompt)
                    st.rerun()

    _render_transcript()

    if st.session_state.get("assistant_running"):
        st.chat_input("Ask about runs, the graph, or run a workflow…", disabled=True)
        return

    if st.session_state.get("assistant_messages", []) and (
        st.session_state["assistant_messages"][-1].get("role") == "user"
    ):
        _run_turn(runtime, llm_cfg, allow_write)
        return

    prompt = st.chat_input("Ask about runs, the graph, or run a workflow…")
    if prompt:
        _submit_prompt(prompt)
        st.rerun()
