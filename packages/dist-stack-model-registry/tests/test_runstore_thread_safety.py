"""Concurrent access: ThreadPoolExecutor(8) x 200 mixed create_run/get_run
against one file DB — no exceptions, correct final counts.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from dist_stack import create_run, get_run, list_runs


def test_concurrent_create_and_get(tmp_path):
    db = tmp_path / "runstore.sqlite"
    baseline = create_run(
        "sim", run_type="erad_simulation", run_id="sim_000000000000", runstore_db=db
    )

    tasks = []
    for i in range(1, 201):
        if i % 2 == 0:
            tasks.append(("create", f"sim_{i:012d}"))  # 100 distinct creates
        else:
            tasks.append(("get", "sim_000000000000"))   # 100 reads

    def run(task):
        kind, run_id = task
        if kind == "create":
            rec = create_run(
                "sim", run_type="erad_simulation", run_id=run_id, runstore_db=db
            )
            return ("ok", rec.run_id)
        rec = get_run(run_id, runstore_db=db)
        return ("ok", rec.run_id)

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(run, tasks):
            results.append(result)

    # No exceptions; all tasks completed.
    assert all(status == "ok" for status, _ in results)

    # Correct final row count: baseline + 100 distinct creates.
    # (list_runs caps at limit=100 by default, so count via SQL for precision.)
    with sqlite3.connect(str(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert n == 101
    assert len(list_runs(include_deleted=True, limit=1000, runstore_db=db)) == 101
    for task in tasks:
        if task[0] == "create":
            assert get_run(task[1], runstore_db=db).run_id == task[1]
    assert get_run("sim_000000000000", runstore_db=db).run_id == baseline.run_id
