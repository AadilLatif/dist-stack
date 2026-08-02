"""Smoke tests for the app's data-access layer (data.py).

These seed throwaway runstore / kg / registry databases through the dist-stack
APIs themselves, then assert that the app's data functions return the expected
rows. The UI framework is deliberately not exercised here.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dist_stack.kg.api import upsert_edge, upsert_node
from dist_stack.registry.api import register
from dist_stack.runstore.api import attach_artifact, create_run

import data


class TestDataLayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runstore_db = self.root / "runstore.db"
        self.kg_db = self.root / "kg.db"
        self.registry_db = self.root / "registry.db"

        self._seed_runstore()
        self._seed_kg()
        self._seed_registry()

        self.cfg = data.Config(
            runstore_db=str(self.runstore_db),
            kg_db=str(self.kg_db),
            registry_db=str(self.registry_db),
        )

    def tearDown(self):
        self.tmp.cleanup()

    # -- seeding ------------------------------------------------------------

    def _seed_runstore(self):
        # simulator run, succeeded, with an artifact
        r1 = create_run(
            "sim",
            run_type="erad_simulation",
            run_id="sim_111111111111",
            status="succeeded",
            message="converged in 40 iterations",
            session_id="sess-alpha",
            model_id="gdm-3.2",
            model_version=1,
            payload={"iterations": 40, "metric": 0.97},
            runstore_db=self.runstore_db,
        )
        create_run(
            "sim",
            run_type="erad_simulation",
            run_id="sim_222222222222",
            status="failed",
            message="solver diverged",
            session_id="sess-alpha",
            runstore_db=self.runstore_db,
        )
        create_run(
            "shift",
            run_type="shift_feeder",
            run_id="shift_333333333333",
            status="running",
            session_id="sess-beta",
            runstore_db=self.runstore_db,
        )

        art = self.root / "out.json"
        art.write_text("{}")
        attach_artifact(r1.run_id, art, runstore_db=self.runstore_db)

    def _seed_kg(self):
        # provenance chain a -> b -> c, plus an artifact + model node
        upsert_node("a", "artifact", label="artifact a", kg_db=self.kg_db)
        upsert_node("b", "artifact", label="artifact b", kg_db=self.kg_db)
        upsert_node("c", "gdm_flow_run", label="run:sim_111111111111", run_id="sim_111111111111", kg_db=self.kg_db)
        upsert_node("m1", "model", label="model gdm-3.2", model_id="gdm-3.2", kg_db=self.kg_db)
        upsert_edge("a", "b", "derived_from", kg_db=self.kg_db)
        upsert_edge("b", "c", "derived_from", kg_db=self.kg_db)
        upsert_edge("c", "m1", "validates", kg_db=self.kg_db)

    def _seed_registry(self):
        register(
            "gdm-3.2",
            version=1,
            stored_path=str(self.root / "models" / "gdm-3.2"),
            model_hash="sha256:abc",
            metadata={"family": "GDM"},
            registry_db=self.registry_db,
            check_exists=False,
        )
        register(
            "gdm-3.2",
            version=2,
            stored_path=str(self.root / "models" / "gdm-3.2-v2"),
            model_hash="sha256:def",
            registry_db=self.registry_db,
            check_exists=False,
        )

    # -- runstore -----------------------------------------------------------

    def test_load_runs_returns_rows_and_filters(self):
        df, has_more = data.load_runs(self.cfg, limit=25)
        self.assertEqual(len(df), 3)
        self.assertFalse(has_more)
        self.assertEqual(set(df["status"]), {"succeeded", "failed", "running"})

        df_failed, _ = data.load_runs(self.cfg, status="failed")
        self.assertEqual(list(df_failed["run_id"]), ["sim_222222222222"])

        df_tool, _ = data.load_runs(self.cfg, tool="shift")
        self.assertEqual(list(df_tool["run_id"]), ["shift_333333333333"])

        df_sess, _ = data.load_runs(self.cfg, session_id="sess-alpha")
        self.assertEqual(len(df_sess), 2)

    def test_load_runs_pagination_reports_has_more(self):
        # 3 rows, page size 2 -> first page should report more
        df, has_more = data.load_runs(self.cfg, limit=2, offset=0)
        self.assertEqual(len(df), 2)
        self.assertTrue(has_more)

    def test_get_run_returns_record_with_payload(self):
        run = data.get_run(self.cfg, "sim_111111111111")
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.payload["iterations"], 40)
        self.assertTrue(run.success)

    def test_load_artifacts(self):
        arts = data.load_artifacts(self.cfg, "sim_111111111111")
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts.iloc[0]["artifact_path"], str(self.root / "out.json"))

    def test_run_filter_options(self):
        opts = data.run_filter_options(self.cfg)
        self.assertEqual(opts["tool"], ["shift", "sim"])
        self.assertEqual(opts["status"], ["failed", "running", "succeeded"])
        self.assertEqual(opts["session_id"], ["sess-alpha", "sess-beta"])

    # -- knowledge graph ----------------------------------------------------

    def test_find_nodes_exact_and_by_label(self):
        exact = data.find_nodes(self.cfg, "c")
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].node_id, "c")

        by_label = data.find_nodes(self.cfg, "sim_111111111111")
        self.assertGreaterEqual(len(by_label), 1)

    def test_provenance_chain_up_and_down(self):
        up = data.load_chain(self.cfg, "c", direction="up", max_depth=5)
        ids = [[n.node_id for n in depth] for depth in up]
        self.assertEqual(ids[0], ["c"])
        self.assertEqual(ids[1], ["b"])
        self.assertEqual(ids[2], ["a"])

        down = data.load_chain(self.cfg, "a", direction="down", max_depth=5)
        ids = [[n.node_id for n in depth] for depth in down]
        self.assertEqual(ids[0], ["a"])
        self.assertEqual(ids[1], ["b"])
        self.assertEqual(ids[2], ["c"])

    def test_neighbors(self):
        edges = data.load_neighbors(self.cfg, "b")
        pairs = sorted((e.source_node, e.target_node) for e in edges)
        self.assertEqual(pairs, [("a", "b"), ("b", "c")])

    def test_search_nodes(self):
        df = data.search_nodes_df(self.cfg, node_type="artifact")
        self.assertEqual(set(df["node_type"]), {"artifact"})
        df2 = data.search_nodes_df(self.cfg, label="model")
        self.assertIn("m1", list(df2["node_id"]))

    def test_graph_stats(self):
        stats = data.load_graph_stats(self.cfg)
        self.assertEqual(stats.node_counts["artifact"], 2)
        self.assertEqual(stats.edge_counts["derived_from"], 2)
        self.assertEqual(len(stats.top_degree) > 0, True)

    # -- registry -----------------------------------------------------------

    def test_load_models(self):
        df = data.load_models(self.cfg)
        self.assertEqual(len(df), 2)
        versions = sorted(df["version"].tolist())
        self.assertEqual(versions, [1, 2])
        self.assertEqual(df.iloc[0]["model_hash"], "sha256:abc")

    # -- robustness ---------------------------------------------------------

    def test_missing_db_raises_and_is_not_created(self):
        missing = data.Config(
            runstore_db=str(self.root / "nope.db"),
            kg_db=str(self.kg_db),
            registry_db=str(self.registry_db),
        )
        with self.assertRaises(data.DataError):
            data.load_runs(missing)
        # read-only contract: a missing store must not be materialized
        self.assertFalse((self.root / "nope.db").exists())

    def test_db_available(self):
        self.assertTrue(data.db_available(str(self.runstore_db)))
        self.assertFalse(data.db_available(str(self.root / "missing.db")))

    def test_resolve_paths_defaults(self):
        for var in ("DIST_STACK_RUNSTORE_DB", "DIST_STACK_KG_DB", "DIST_STACK_MODEL_REGISTRY_DB"):
            os.environ.pop(var, None)
        cfg = data.resolve_paths()
        home_cache = data.cache_dir()
        self.assertEqual(cfg.runstore_db, str(home_cache / "runstore.db"))
        self.assertEqual(cfg.kg_db, str(home_cache / "kg.db"))
        self.assertEqual(cfg.registry_db, str(home_cache / "model_registry.db"))

    def test_resolve_paths_override_beats_env(self):
        os.environ["DIST_STACK_RUNSTORE_DB"] = "/env/path/runstore.db"
        cfg = data.resolve_paths(runstore_override="/override/runstore.db")
        self.assertEqual(cfg.runstore_db, "/override/runstore.db")
        cfg2 = data.resolve_paths()
        self.assertEqual(cfg2.runstore_db, "/env/path/runstore.db")
        os.environ.pop("DIST_STACK_RUNSTORE_DB", None)


if __name__ == "__main__":
    unittest.main()
