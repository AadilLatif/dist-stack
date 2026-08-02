"""Visual layer: a single injected stylesheet plus small HTML helpers.

The app is neutral-first: warm paper background, ink text, one dark-petrol
accent, and a reserved set of semantic colors for status badges. Type is
Fraunces (display) + Inter (body) + IBM Plex Mono (identifiers), loaded from
Google Fonts with safe system fallbacks so the app still looks right offline.
"""

from __future__ import annotations

import streamlit as st

# --- palette ----------------------------------------------------------------
ACCENT = "#14555A"
INK = "#221F1A"
MUTED = "#7C766B"
LINE = "#E5DFD4"

STATUS_STYLES = {
    "succeeded": ("#E7F3EA", "#1B7A3D", "#C3E2CB"),
    "failed": ("#FBE9E7", "#B3261E", "#F2C1BD"),
    "running": ("#E8F0FE", "#1A56DB", "#BFD2F7"),
    "pending": ("#F1EFEC", "#57534E", "#DBD6CE"),
    "cancelled": ("#FDF0E2", "#B45309", "#F4D3A8"),
}

NODE_TYPE_STYLES = {
    "gdm_system": ("#EDE7F6", "#4A3F7A", "#D8CFF0"),
    "component": ("#E3EAF1", "#2E5A7F", "#C6D6E4"),
    "artifact": ("#E7F3EA", "#1B7A3D", "#C3E2CB"),
    "model": ("#FDF0E2", "#B45309", "#F4D3A8"),
}

_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {{
  --paper: #F6F3EE;
  --panel: #FFFFFF;
  --ink: {INK};
  --ink-2: #4A453D;
  --muted: {MUTED};
  --line: {LINE};
  --accent: {ACCENT};
  --accent-soft: #E3EFF0;
}}

html, body, .stApp {{
  background: var(--paper);
  color: var(--ink);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}}

[data-testid="stHeader"] {{ background: transparent; }}

[data-testid="stAppViewContainer"] > .main .block-container {{
  padding-top: 2.4rem;
  padding-bottom: 5rem;
  max-width: 1120px;
}}

[data-testid="stSidebar"] {{
  background: #FBFAF7;
  border-right: 1px solid var(--line);
}}
[data-testid="stSidebar"] .block-container {{ padding-top: 1.6rem; }}

/* headings ------------------------------------------------------------------ */
h1, h2, h3, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {{
  font-family: 'Fraunces', Georgia, 'Times New Roman', serif;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ink);
}}
h1 {{ font-size: 1.9rem; line-height: 1.15; }}
h2 {{ font-size: 1.35rem; }}
h3 {{ font-size: 1.05rem; }}

p, li, .stMarkdown {{ color: var(--ink-2); }}
[data-testid="stMarkdownContainer"] p {{ line-height: 1.55; }}

/* widgets ------------------------------------------------------------------- */
.stTextInput input, .stSelectbox [data-baseweb="select"] > div,
.stNumberInput input, .stTextArea textarea {{
  background: var(--panel);
  border-radius: 8px;
}}
[data-baseweb="select"] * {{ border-color: var(--line) !important; }}

.stButton > button {{
  border-radius: 8px;
  font-weight: 500;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--ink);
  transition: border-color .15s ease, background .15s ease;
}}
.stButton > button:hover {{ border-color: var(--accent); color: var(--accent); }}
.stButton > button[kind="primary"] {{
  background: var(--accent);
  border-color: var(--accent);
  color: #FDFCFA;
}}
.stButton > button[kind="primary"]:hover {{ background: #0F4549; color: #FDFCFA; }}

[data-testid="stRadio"] label {{
  font-size: 0.92rem;
  padding: 0.18rem 0.5rem;
  border-radius: 7px;
  color: var(--ink-2);
}}
[data-testid="stRadio"] label:hover {{ background: #F0EDE7; }}

.stExpander {{ border: 1px solid var(--line) !important; border-radius: 10px !important; }}

/* dataframes ----------------------------------------------------------------- */
[data-testid="stDataFrame"] {{
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}}
[data-testid="stDataFrame"] * {{ font-size: 0.84rem; }}

/* captions / small print */
[data-testid="stCaptionContainer"], .stCaption {{
  color: var(--muted);
  font-size: 0.8rem;
}}

/* badges -------------------------------------------------------------------- */
.badge {{
  display: inline-block;
  padding: 1px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: .02em;
  line-height: 1.6;
  border: 1px solid transparent;
  font-family: 'Inter', sans-serif;
  white-space: nowrap;
}}
.badge--type {{ background: #EFEDE8; color: #57534E; border-color: #DDD8CF; }}
"""

for _name, (_bg, _fg, _bd) in STATUS_STYLES.items():
    _CSS += f".badge--{_name} {{ background:{_bg}; color:{_fg}; border-color:{_bd}; }}\n"
for _name, (_bg, _fg, _bd) in NODE_TYPE_STYLES.items():
    _CSS += f".badge--type-{_name} {{ background:{_bg}; color:{_fg}; border-color:{_bd}; }}\n"

_CSS += """
/* stat cards ----------------------------------------------------------------- */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 10px;
  margin: 0.4rem 0 1.2rem;
}
.stat-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px 10px;
}
.stat-card .k {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .07em;
  font-weight: 600;
}
.stat-card .v {
  font-size: 26px;
  font-weight: 600;
  font-family: 'Fraunces', Georgia, serif;
  margin-top: 2px;
  color: var(--ink);
}
.stat-card .s { margin-top: 6px; }

/* provenance tree ------------------------------------------------------------ */
.prov-root { margin: 0.6rem 0; }
.prov-depth {
  margin-left: 20px;
  padding-left: 16px;
  border-left: 1px solid var(--line);
}
.prov-node {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 0;
}
.prov-node .id { font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace; font-size: 0.84rem; color: var(--ink); }
.prov-node .meta { font-size: 0.8rem; color: var(--muted); }
.prov-node .depth-hint { font-size: 11px; color: #A69F93; width: 44px; flex: none; }

/* empty state ---------------------------------------------------------------- */
.empty-state {
  border: 1px dashed #CFC7B8;
  border-radius: 12px;
  background: #FBFAF7;
  padding: 22px 26px;
  margin: 0.6rem 0 1.2rem;
  color: var(--ink-2);
}
.empty-state .t { font-family: 'Fraunces', Georgia, serif; font-size: 1.05rem; font-weight: 600; color: var(--ink); }
.empty-state .d { font-size: 0.86rem; color: var(--muted); margin-top: 4px; }

/* source list (data sources panel) ------------------------------------------- */
.src-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 0;
  font-size: 0.84rem;
}
.src-row .path { font-family: 'IBM Plex Mono', ui-monospace, monospace; color: var(--ink-2); word-break: break-all; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; align-self: center; }
.dot--ok { background: #1B7A3D; }
.dot--missing { background: #C9C2B6; }

/* key/value detail ----------------------------------------------------------- */
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 22px;
  margin: 0.4rem 0 0.8rem;
  font-size: 0.86rem;
}
.kv .k { color: var(--muted); }
.kv .v { color: var(--ink-2); word-break: break-word; }
.kv .v.mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }

code {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 0.85em;
  background: #EFECE6;
  border-radius: 5px;
  padding: 0.1em 0.4em;
}
"""


def inject() -> None:
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# --- HTML helpers ------------------------------------------------------------


def badge(text: str, kind: str | None = None) -> str:
    """A status or node-type pill."""
    cls = f"badge--{kind}" if kind else "badge--type"
    return f'<span class="badge {cls}">{text}</span>'


def status_badge(status: str) -> str:
    return badge(status, status)


def stat_card(label: str, value, *, status: str | None = None, sub: str | None = None) -> str:
    extra = f'<div class="s">{status_badge(status) if status else (sub or "")}</div>'
    return f'<div class="stat-card"><div class="k">{label}</div><div class="v">{value}</div>{extra}</div>'


def card_grid(cards: list[str]) -> None:
    st.markdown(f'<div class="card-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def empty_state(title: str, detail: str) -> None:
    st.markdown(
        f'<div class="empty-state"><div class="t">{title}</div>'
        f'<div class="d">{detail}</div></div>',
        unsafe_allow_html=True,
    )


def kv_rows(pairs: list[tuple[str, str]], *, mono: bool = False) -> None:
    rows = "".join(
        f'<div class="k">{k}</div><div class="v{" mono" if mono else ""}">{v or "—"}</div>'
        for k, v in pairs
    )
    st.markdown(f'<div class="kv">{rows}</div>', unsafe_allow_html=True)
