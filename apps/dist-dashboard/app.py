"""dist-stack visibility — a read-only browser over the dist-stack ecosystem.

Everything reads from SQLite via the dist-stack Python APIs (runstore, kg,
registry). Nothing here writes.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
import os
import sys

# Make sibling modules importable no matter which directory the app is
# launched from (streamlit run <path>/app.py, AppTest, bare python -c import).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

import data
import styles
from data import Config, DataError

PAGES = [
    ("dashboard", "Dashboard"),
    ("runs", "Run History"),
    ("provenance", "Provenance"),
    ("graph", "Knowledge Graph"),
    ("registry", "Registry"),
]
PAGE_IDS = [p for p, _ in PAGES]
PAGE_LABELS = dict(PAGES)

# ---------------------------------------------------------------------------
# Shared bits
# ---------------------------------------------------------------------------


def make_config() -> Config:
    return data.resolve_paths(
        runstore_override=st.session_state.get("db_runstore"),
        kg_override=st.session_state.get("db_kg"),
        registry_override=st.session_state.get("db_registry"),
    )


def source_row(name: str, path: str) -> str:
    ok = data.db_available(path)
    cls = "ok" if ok else "missing"
    return (
        f'<div class="src-row"><span class="dot dot--{cls}"></span>'
        f"<span>{name}</span><span class=\"path\">{path}</span></div>"
    )


def fmt_ts(value) -> str:
    if not value:
        return ""
    return str(value)[:19].replace("T", " ")


def style_runs(df: pd.DataFrame) -> pd.DataFrame | "pd.io.formats.style.Styler":
    if df.empty:
        return df

    def _status_css(v):
        if v in styles.STATUS_STYLES:
            bg, fg, _ = styles.STATUS_STYLES[v]
            return f"background-color:{bg}; color:{fg}; font-weight:600;"
        return ""

    def _mono_css(_v):
        return "font-family:'IBM Plex Mono', ui-monospace, monospace; font-size:0.82rem;"

    styled = df.style.map(_status_css, subset=["status"])
    for col in ("run_id", "session_id", "model_id"):
        if col in df.columns:
            styled = styled.map(_mono_css, subset=[col])
    return styled


def empty_store(kind: str, path: str) -> None:
    styles.empty_state(
        f"No {kind} found",
        f"Nothing to show yet. Point the store at an existing database — the "
        f"current path is <code>{path}</code>. Edit the path in Settings or set "
        f"the matching <code>DIST_STACK_*</code> environment variable.",
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_dashboard(cfg: Config) -> None:
    st.title("Overview")
    st.caption(
        "The recorded state of the dist-stack: run results, the knowledge "
        "graph built from them, and registered models. Everything is read-only."
    )

    st.markdown("### Data sources")
    st.markdown(
        '<div style="margin-top:0.5rem">'
        + "".join(
            [
                source_row("runstore", cfg.runstore_db),
                source_row("knowledge graph", cfg.kg_db),
                source_row("model registry", cfg.registry_db),
            ]
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("### Runs")
        if not data.db_available(cfg.runstore_db):
            empty_store("runstore", cfg.runstore_db)
        else:
            try:
                all_runs = data.load_runs_all(cfg)
            except DataError as exc:
                styles.empty_state("Could not read runstore", str(exc))
                all_runs = None
            if all_runs is not None:
                counts = data.counts_by_status(all_runs)
                cards = [
                    styles.stat_card("Total runs", len(all_runs)),
                    styles.stat_card("Succeeded", counts["succeeded"], status="succeeded"),
                    styles.stat_card("Failed", counts["failed"], status="failed"),
                    styles.stat_card("Running", counts["running"], status="running"),
                    styles.stat_card("Pending", counts["pending"], status="pending"),
                    styles.stat_card("Cancelled", counts["cancelled"], status="cancelled"),
                ]
                styles.card_grid(cards)

                st.markdown("**Recent runs**")
                recent = all_runs.head(8).copy()
                recent["created"] = recent["created_at_utc"].map(fmt_ts)
                event = st.dataframe(
                    style_runs(recent[["run_id", "created", "tool", "run_type", "status", "message"]]),
                    key="dash_recent",
                    on_select="rerun",
                    selection_mode="single-row",
                    width="stretch",
                    height=330,
                    hide_index=True,
                    column_config={
                        "run_id": st.column_config.TextColumn("Run id", width="medium"),
                        "created": st.column_config.TextColumn("Created (UTC)", width="medium"),
                        "tool": st.column_config.TextColumn("Tool", width="small"),
                        "run_type": st.column_config.TextColumn("Type", width="small"),
                        "status": st.column_config.TextColumn("Status", width="small"),
                        "message": st.column_config.TextColumn("Message", width="large"),
                    },
                )
                if event and event.selection.rows:
                    run_id = recent.iloc[event.selection.rows[0]]["run_id"]
                    st.session_state["_focus_run"] = run_id
                    st.session_state["_goto"] = "runs"
                    st.rerun()

    with right:
        st.markdown("### Knowledge graph")
        if not data.db_available(cfg.kg_db):
            empty_store("knowledge graph", cfg.kg_db)
        else:
            try:
                stats = data.load_graph_stats(cfg)
            except DataError as exc:
                styles.empty_state("Could not read knowledge graph", str(exc))
                stats = None
            if stats is not None:
                type_cards = [
                    styles.stat_card(t or "?", n)
                    for t, n in sorted(stats.node_counts.items())
                ]
                if type_cards:
                    styles.card_grid(type_cards)
                else:
                    st.caption("No nodes recorded.")
                st.markdown("**Most connected nodes**")
                if stats.top_degree:
                    deg = pd.DataFrame(stats.top_degree[:5], columns=["node_id", "degree"])
                    st.dataframe(
                        deg.style.map(
                            lambda _v: "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;",
                            subset=["node_id"],
                        ),
                        width="stretch",
                        height=210,
                        hide_index=True,
                    )
                else:
                    st.caption("No edges recorded.")

        st.markdown("### Model registry")
        if not data.db_available(cfg.registry_db):
            empty_store("model registry", cfg.registry_db)
        else:
            try:
                models = data.load_models(cfg)
            except DataError as exc:
                styles.empty_state("Could not read model registry", str(exc))
                models = None
            if models is not None:
                styles.card_grid([styles.stat_card("Registered models", len(models))])
                if not models.empty:
                    st.markdown("**Latest registrations**")
                    latest = models.sort_values("created_at_utc", ascending=False).head(5)
                    st.dataframe(
                        latest[["model_id", "version", "created_at_utc"]].style.map(
                            lambda _v: "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;",
                            subset=["model_id"],
                        ),
                        width="stretch",
                        height=210,
                        hide_index=True,
                        column_config={
                            "created_at_utc": st.column_config.TextColumn("Registered (UTC)", width="medium"),
                        },
                    )


def page_runs(cfg: Config) -> None:
    st.title("Run History")
    st.caption(
        "Every run recorded in the runstore, newest first. Filter, page "
        "through, and click a row to open its detail."
    )

    if not data.db_available(cfg.runstore_db):
        empty_store("runstore", cfg.runstore_db)
        return

    try:
        options = data.run_filter_options(cfg)
    except DataError as exc:
        styles.empty_state("Could not read runstore", str(exc))
        return

    page_size = data.RUN_PAGE_SIZE

    # A run opened from elsewhere (dashboard) shows its detail directly.
    focus_run = st.session_state.pop("_focus_run", None)

    # --- filters -----------------------------------------------------------
    f_tool, f_type, f_status, f_session, _spacer = st.columns([1, 1, 1, 1, 2])
    with f_tool:
        tool = st.selectbox("Tool", ["All"] + options["tool"], key="run_f_tool")
    with f_type:
        run_type = st.selectbox("Run type", ["All"] + options["run_type"], key="run_f_type")
    with f_status:
        status = st.selectbox("Status", ["All"] + options["status"], key="run_f_status")
    with f_session:
        session_id = st.selectbox("Session", ["All"] + options["session_id"], key="run_f_session")

    if st.button("Clear filters", type="secondary"):
        for k in ("run_f_tool", "run_f_type", "run_f_status", "run_f_session"):
            st.session_state[k] = "All"
        st.rerun()

    kwargs = {}
    if tool != "All":
        kwargs["tool"] = tool
    if run_type != "All":
        kwargs["run_type"] = run_type
    if status != "All":
        kwargs["status"] = status
    if session_id != "All":
        kwargs["session_id"] = session_id

    # A changed filter jumps back to the first page.
    filter_sig = "|".join([tool, run_type, status, session_id])
    if st.session_state.get("runs_filter_sig") != filter_sig:
        st.session_state["runs_filter_sig"] = filter_sig
        st.session_state["runs_page"] = 0

    # --- pagination --------------------------------------------------------
    page = st.session_state.get("runs_page", 0)

    prev_col, page_col, next_col = st.columns([1, 3, 1])
    with prev_col:
        if st.button("Previous", disabled=(page == 0)):
            st.session_state["runs_page"] = max(0, page - 1)
            st.rerun()
    with page_col:
        st.caption(f"Page {page + 1}")
    with next_col:
        if st.button("Next"):
            st.session_state["runs_page"] = page + 1
            st.rerun()

    try:
        df, has_more = data.load_runs(cfg, offset=page * page_size, limit=page_size, **kwargs)
    except DataError as exc:
        styles.empty_state("Could not read runstore", str(exc))
        return

    st.caption(
        f"{len(df)} run(s) on this page"
        + ("" if not has_more else " — more below, use Next")
    )

    if df.empty:
        if page > 0 and not has_more:
            styles.empty_state("No more runs", "You have reached the end of the results — use Previous.")
        else:
            styles.empty_state("No runs match", "Adjust the filters above to widen the search.")
        return

    # --- detail for a focused run ------------------------------------------
    if focus_run:
        try:
            run = data.get_run(cfg, focus_run)
            render_run_detail(cfg, run)
            st.divider()
        except DataError:
            st.caption(f"Run {focus_run} is not in this store.")

    # --- table -------------------------------------------------------------
    table = df.copy()
    table["created"] = table["created_at_utc"].map(fmt_ts)
    shown = table[["run_id", "created", "tool", "run_type", "status", "implementation", "session_id", "message"]]

    fkey = "|".join(map(str, [tool, run_type, status, session_id, page]))
    event = st.dataframe(
        style_runs(shown),
        key=f"runs_table_{fkey}",
        on_select="rerun",
        selection_mode="single-row",
        width="stretch",
        height=430,
        hide_index=True,
        column_config={
            "run_id": st.column_config.TextColumn("Run id", width="medium"),
            "created": st.column_config.TextColumn("Created (UTC)", width="medium"),
            "tool": st.column_config.TextColumn("Tool", width="small"),
            "run_type": st.column_config.TextColumn("Type", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "implementation": st.column_config.TextColumn("Impl", width="small"),
            "session_id": st.column_config.TextColumn("Session", width="small"),
            "message": st.column_config.TextColumn("Message", width="large"),
        },
    )

    if event and event.selection.rows:
        run_id = shown.iloc[event.selection.rows[0]]["run_id"]
        try:
            run = data.get_run(cfg, run_id)
        except DataError as exc:
            styles.empty_state("Could not load run", str(exc))
            return
        st.markdown("---")
        render_run_detail(cfg, run)


def render_run_detail(cfg: Config, run) -> None:
    st.markdown(
        f"### Run {run.run_id} {styles.status_badge(run.status)}"
        f"<span style=\"font-size:0.85rem;color:var(--muted);margin-left:10px\">"
        f"{run.tool} · {run.run_type}</span>",
        unsafe_allow_html=True,
    )
    styles.kv_rows(
        [
            ("Created", fmt_ts(run.created_at_utc)),
            ("Updated", fmt_ts(run.updated_at_utc)),
            ("Tool version", run.tool_version),
            ("Implementation", run.implementation),
            ("Session", run.session_id),
            ("Model", run.model_id),
            ("Model version", run.model_version),
            ("Model hash", run.model_hash),
        ],
        mono=True,
    )

    if run.message:
        st.markdown("**Message**")
        st.markdown(run.message)

    with st.expander("Payload"):
        if run.payload:
            st.json(run.payload, expanded=True)
        else:
            st.caption("No payload recorded for this run.")

    st.markdown("**Artifacts**")
    try:
        arts = data.load_artifacts(cfg, run.run_id)
    except DataError as exc:
        styles.empty_state("Could not read artifacts", str(exc))
        arts = pd.DataFrame()
    if arts.empty:
        st.caption("No artifacts attached to this run.")
    else:
        st.dataframe(
            arts.style.map(
                lambda _v: "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;",
                subset=["artifact_id", "artifact_path"],
            ),
            width="stretch",
            height=min(360, 90 + 34 * len(arts)),
            hide_index=True,
            column_config={
                "artifact_id": st.column_config.TextColumn("Artifact id", width="small"),
                "artifact_path": st.column_config.TextColumn("Path", width="large"),
                "artifact_type": st.column_config.TextColumn("Type", width="small"),
                "tool": st.column_config.TextColumn("Tool", width="small"),
                "created_at_utc": st.column_config.TextColumn("Created (UTC)", width="medium"),
            },
        )

    if st.button(f"Open provenance for run:{run.run_id}"):
        st.session_state["_prov_node"] = f"run:{run.run_id}"
        st.session_state["_goto"] = "provenance"
        st.rerun()


def _chain_html(chain: list[list], current_id: str) -> str:
    parts = []
    for depth, nodes in enumerate(chain):
        parts.append('<div class="prov-root">' if depth == 0 else '<div class="prov-depth">')
        for n in nodes:
            if n.node_type == "artifact" and n.artifact_path:
                meta = n.artifact_path
            elif n.label:
                meta = n.label
            else:
                meta = n.run_id or n.model_id or ""
            is_root = depth == 0 and n.node_id == current_id
            row = (
                f'<div class="prov-node"><span class="depth-hint">d{depth}</span>'
                f'{styles.badge(n.node_type, f"type-{n.node_type}")}'
                f'<span class="id">{n.node_id}</span>'
                f'<span class="meta">{meta}</span>'
            )
            if is_root:
                row += '<span class="meta" style="font-weight:600;color:var(--accent)">start</span>'
            row += "</div>"
            parts.append(row)
    parts.append("</div>" * len(chain))
    return "".join(parts)


def page_provenance(cfg: Config) -> None:
    st.title("Provenance")
    st.caption(
        "Trace where a node came from (upstream) or what it produced "
        "(downstream), then inspect its immediate neighbors."
    )

    prov_prefill = st.session_state.pop("_prov_node", None)
    if prov_prefill:
        st.session_state["prov_term"] = prov_prefill
        st.session_state.pop("prov_results", None)
        st.session_state.pop("prov_searched", None)

    term = st.text_input(
        "Node id, or a run id / artifact path / model id to search for",
        key="prov_term",
        placeholder="e.g. run:sim_1a2b3c4d5e6f or artifact:/path/out.json",
    )

    if not data.db_available(cfg.kg_db):
        empty_store("knowledge graph", cfg.kg_db)
        return

    results = None
    if term:
        ctx = (term, cfg.kg_db)
        if st.session_state.get("prov_searched") != ctx:
            results = data.find_nodes(cfg, term)
            st.session_state["prov_results"] = results
            st.session_state["prov_searched"] = ctx
        else:
            results = st.session_state.get("prov_results", [])

        if not results:
            styles.empty_state("No node matches", f"Nothing found for <code>{term}</code>.")
            return

        if len(results) > 1:
            labels = {
                n.node_id: f"{n.node_id}  ({n.node_type} · {n.label or 'no label'})"
                for n in results
            }
            chosen = st.selectbox(
                "Several nodes match — pick one",
                [n.node_id for n in results],
                format_func=lambda nid: labels[nid],
                key="prov_pick",
            )
            nodes = [n for n in results if n.node_id == chosen]
        else:
            nodes = results

        node = nodes[0]

        st.markdown("---")
        st.markdown(
            f"### {styles.badge(node.node_type, f'type-{node.node_type}')} "
            f"<span style=\"font-family:'IBM Plex Mono',monospace\">{node.node_id}</span>",
            unsafe_allow_html=True,
        )
        styles.kv_rows(
            [
                ("Label", node.label),
                ("Run id", node.run_id),
                ("Artifact path", node.artifact_path),
                ("Model id", node.model_id),
                ("Tool", f"{node.tool} {node.tool_version or ''}".strip()),
                ("Created", fmt_ts(node.created_at_utc)),
            ],
            mono=True,
        )
        with st.expander("Node metadata"):
            if node.metadata:
                st.json(node.metadata, expanded=True)
            else:
                st.caption("No metadata recorded.")

        ctrl_l, ctrl_r = st.columns([1, 2])
        with ctrl_l:
            direction = st.radio("Trace direction", ["up", "down"], key="prov_dir", horizontal=True)
        with ctrl_r:
            max_depth = st.slider("Max depth", 1, 10, 3, key="prov_depth")

        heading = "Ancestors" if direction == "up" else "Descendants"
        try:
            chain = data.load_chain(cfg, node.node_id, direction, max_depth)
        except DataError as exc:
            styles.empty_state("Could not read knowledge graph", str(exc))
            chain = None

        if chain is not None:
            st.markdown(f"**{heading}**")
            if len(chain) <= 1:
                st.caption(
                    "No upstream provenance recorded."
                    if direction == "up"
                    else "No downstream relations recorded."
                )
            else:
                st.markdown(_chain_html(chain, node.node_id), unsafe_allow_html=True)

        # --- neighbors ------------------------------------------------------
        st.markdown("---")
        st.markdown("**Neighbors**")
        n_rel, n_dir, n_spacer = st.columns([1, 1, 3])
        with n_rel:
            rel = st.selectbox("Relation", ["All"] + data.RELATIONS, key="nb_rel")
        with n_dir:
            ndir = st.selectbox("Direction", ["both", "in", "out"], key="nb_dir")
        try:
            edges = data.load_neighbors(
                cfg, node.node_id, relation=None if rel == "All" else rel, direction=ndir
            )
        except DataError as exc:
            styles.empty_state("Could not read knowledge graph", str(exc))
            edges = []

        if not edges:
            st.caption("No neighbors found with these filters.")
        else:
            rows = []
            for e in edges:
                other = e.source_node if e.target_node == node.node_id else e.target_node
                direction_label = "in" if e.target_node == node.node_id else "out"
                try:
                    other_node = data.get_node(cfg, other)
                    ntype, nlabel = other_node.node_type, other_node.label or ""
                except DataError:
                    ntype, nlabel = "", ""
                rows.append(
                    {
                        "relation": e.relation,
                        "direction": direction_label,
                        "node_id": other,
                        "node_type": ntype,
                        "label": nlabel,
                    }
                )
            nb = pd.DataFrame(rows)
            st.dataframe(
                nb.style.map(
                    lambda _v: "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;",
                    subset=["node_id"],
                ),
                width="stretch",
                height=min(420, 90 + 34 * len(nb)),
                hide_index=True,
                column_config={
                    "relation": st.column_config.TextColumn("Relation", width="small"),
                    "direction": st.column_config.TextColumn("Dir", width="small"),
                    "node_id": st.column_config.TextColumn("Neighbor node", width="medium"),
                    "node_type": st.column_config.TextColumn("Type", width="small"),
                    "label": st.column_config.TextColumn("Label", width="large"),
                },
            )


def page_graph(cfg: Config) -> None:
    st.title("Knowledge Graph")
    st.caption(
        "What the graph currently holds: node and edge counts, and a search "
        "over nodes by type and label."
    )

    if not data.db_available(cfg.kg_db):
        empty_store("knowledge graph", cfg.kg_db)
        return

    try:
        stats = data.load_graph_stats(cfg)
    except DataError as exc:
        styles.empty_state("Could not read knowledge graph", str(exc))
        stats = None

    if stats is not None:
        st.markdown("### Nodes by type")
        if stats.node_counts:
            styles.card_grid(
                [styles.stat_card(t, n) for t, n in sorted(stats.node_counts.items())]
            )
        else:
            st.caption("No nodes recorded.")

        ecol, tcol = st.columns([2, 3], gap="large")
        with ecol:
            st.markdown("### Edges by relation")
            if stats.edge_counts:
                edges = pd.DataFrame(
                    sorted(stats.edge_counts.items()), columns=["relation", "count"]
                )
                st.dataframe(edges, width="stretch", height=320, hide_index=True)
            else:
                st.caption("No edges recorded.")
        with tcol:
            st.markdown("### Most connected")
            if stats.top_degree:
                deg = pd.DataFrame(stats.top_degree, columns=["node_id", "degree"])
                st.dataframe(
                    deg.style.map(
                        lambda _v: "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;",
                        subset=["node_id"],
                    ),
                    width="stretch",
                    height=320,
                    hide_index=True,
                )
            else:
                st.caption("No edges recorded.")

        st.markdown("---")

    st.markdown("### Search nodes")
    with st.form("node_search"):
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            ntype = st.selectbox("Node type", ["All"] + data.NODE_TYPES, key="g_type")
        with c2:
            label = st.text_input("Label contains", key="g_label")
        with c3:
            st.write("")
            st.write("")
            submitted = st.form_submit_button("Search", type="primary")

    if submitted or st.session_state.get("g_results") is not None:
        if submitted:
            st.session_state["g_results"] = (
                None if label.strip() == "" else label.strip(),
                None if ntype == "All" else ntype,
            )
        term, node_type = st.session_state["g_results"]
        try:
            df = data.search_nodes_df(cfg, node_type=node_type, label=term, limit=200)
        except DataError as exc:
            styles.empty_state("Could not read knowledge graph", str(exc))
            return

        st.caption(f"{len(df)} node(s)")
        if df.empty:
            styles.empty_state("No nodes match", "Try a different type or label.")
            return

        event = st.dataframe(
            style_nodes(df),
            key="graph_results",
            on_select="rerun",
            selection_mode="single-row",
            width="stretch",
            height=420,
            hide_index=True,
            column_config={
                "node_id": st.column_config.TextColumn("Node id", width="medium"),
                "node_type": st.column_config.TextColumn("Type", width="small"),
                "label": st.column_config.TextColumn("Label", width="large"),
                "run_id": st.column_config.TextColumn("Run id", width="medium"),
                "model_id": st.column_config.TextColumn("Model id", width="medium"),
                "created_at_utc": st.column_config.TextColumn("Created (UTC)", width="medium"),
            },
        )
        if event and event.selection.rows:
            selected = df.iloc[event.selection.rows[0]]
            st.markdown("---")
            st.markdown(
                f"**Selected:** <span style=\"font-family:'IBM Plex Mono',monospace\">"
                f"{selected['node_id']}</span>",
                unsafe_allow_html=True,
            )
            if st.button("Open provenance for this node"):
                st.session_state["_prov_node"] = selected["node_id"]
                st.session_state["_goto"] = "provenance"
                st.rerun()


def style_nodes(df: pd.DataFrame) -> pd.DataFrame | "pd.io.formats.style.Styler":
    if df.empty:
        return df

    def _mono_css(_v):
        return "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;"

    styled = df.style
    for col in ("node_id", "run_id", "model_id", "artifact_path"):
        if col in df.columns:
            styled = styled.map(_mono_css, subset=[col])
    return styled


def page_registry(cfg: Config) -> None:
    st.title("Model Registry")
    st.caption("Model versions registered in the store, with stored paths and hashes.")

    if not data.db_available(cfg.registry_db):
        empty_store("model registry", cfg.registry_db)
        return

    try:
        df = data.load_models(cfg)
    except DataError as exc:
        styles.empty_state("Could not read model registry", str(exc))
        return

    if df.empty:
        styles.empty_state("Registry is empty", "No model versions have been registered yet.")
        return

    q = st.text_input("Filter by model id", key="reg_q", placeholder="substring of model id")
    if q.strip():
        df = df[df["model_id"].str.contains(q.strip(), case=False, na=False)].reset_index(drop=True)

    st.caption(f"{len(df)} model version(s)")
    event = st.dataframe(
        style_models(df),
        key="reg_table",
        on_select="rerun",
        selection_mode="single-row",
        width="stretch",
        height=430,
        hide_index=True,
        column_config={
            "model_id": st.column_config.TextColumn("Model id", width="large"),
            "version": st.column_config.NumberColumn("Version", width="small"),
            "stored_path": st.column_config.TextColumn("Stored path", width="large"),
            "model_hash": st.column_config.TextColumn("Hash", width="medium"),
            "created_at_utc": st.column_config.TextColumn("Registered (UTC)", width="medium"),
        },
    )

    if event and event.selection.rows:
        row = df.iloc[event.selection.rows[0]]
        st.markdown("---")
        st.markdown(
            f"**{row['model_id']}** · version {int(row['version'])}",
        )
        styles.kv_rows(
            [
                ("Stored path", row["stored_path"]),
                ("Hash", row["model_hash"]),
                ("Registered", fmt_ts(row["created_at_utc"])),
            ],
            mono=True,
        )
        with st.expander("Metadata"):
            if row.get("metadata"):
                try:
                    st.json(json.loads(row["metadata"]), expanded=True)
                except (TypeError, ValueError):
                    st.code(row["metadata"], language=None)
            else:
                st.caption("No metadata recorded.")


def style_models(df: pd.DataFrame) -> pd.DataFrame | "pd.io.formats.style.Styler":
    if df.empty:
        return df

    def _mono_css(_v):
        return "font-family:'IBM Plex Mono', monospace; font-size:0.82rem;"

    styled = df.style
    for col in ("model_id", "stored_path", "model_hash"):
        if col in df.columns:
            styled = styled.map(_mono_css, subset=[col])
    return styled


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------


def render_sidebar() -> str:
    st.sidebar.markdown("#### dist-stack")
    st.sidebar.caption("visibility tier · read-only")

    st.sidebar.markdown("---")
    nav = st.sidebar.radio(
        "View",
        PAGE_IDS,
        format_func=lambda pid: PAGE_LABELS[pid],
        index=PAGE_IDS.index(st.session_state["nav_page"]),
        key="nav_page",
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Settings**")
    defaults = data.resolve_paths()
    st.sidebar.text_input(
        "Runstore DB",
        value=defaults.runstore_db,
        key="db_runstore",
        help="DIST_STACK_RUNSTORE_DB",
    )
    st.sidebar.text_input(
        "Knowledge graph DB",
        value=defaults.kg_db,
        key="db_kg",
        help="DIST_STACK_KG_DB",
    )
    st.sidebar.text_input(
        "Model registry DB",
        value=defaults.registry_db,
        key="db_registry",
        help="DIST_STACK_MODEL_REGISTRY_DB",
    )
    st.sidebar.caption(
        "Fields fall back to the DIST_STACK_* environment variables, then to "
        "~/.cache/dist-stack."
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Read-only. All data is served from the three SQLite stores through "
        "the dist-stack APIs; this app writes nothing."
    )
    return nav


def main() -> None:
    st.set_page_config(page_title="dist-stack · visibility", layout="wide")
    styles.inject()

    if "nav_page" not in st.session_state:
        st.session_state["nav_page"] = "dashboard"

    _goto = st.session_state.pop("_goto", None)
    if _goto in PAGE_IDS:
        st.session_state["nav_page"] = _goto

    nav = render_sidebar()
    cfg = make_config()

    pages = {
        "dashboard": page_dashboard,
        "runs": page_runs,
        "provenance": page_provenance,
        "graph": page_graph,
        "registry": page_registry,
    }
    pages[nav](cfg)

    st.markdown("---")
    st.caption("dist-stack visibility — read-only browsing of recorded run state.")


if __name__ == "__main__":
    main()
