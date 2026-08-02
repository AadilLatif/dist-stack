"""Concurrent access: ThreadPoolExecutor(8) x 200 against one file DB (§8 item 5)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from dist_stack import list_models, lookup, register


def make_file(tmp_path, name="model.json"):
    p = tmp_path / name
    p.write_text("{}")
    return p


def test_concurrent_register_and_lookup(tmp_path):
    db = tmp_path / "registry.sqlite"
    model_file = make_file(tmp_path)

    baseline = register("baseline", stored_path=model_file, registry_db=db)

    tasks = []
    for i in range(200):
        if i % 2 == 0:
            tasks.append(("register", f"m{i}"))
        else:
            tasks.append(("lookup", "baseline"))

    def run(task):
        kind, model_id = task
        if kind == "register":
            rec = register(model_id, stored_path=model_file, registry_db=db)
            return ("ok", rec.model_id, rec.version)
        rec = lookup("baseline", registry_db=db)
        return ("ok", rec.model_id, rec.version)

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(run, tasks):
            results.append(result)

    # No exceptions; all register/lookup tasks completed.
    assert all(status == "ok" for status, _, _ in results)
    assert all(version == 1 for _, _, version in results)

    # Correct final row count: baseline + 100 distinct registers.
    assert len(list_models(include_deleted=True, registry_db=db)) == 101
    for mid in [t[1] for t in tasks if t[0] == "register"]:
        assert lookup(mid, registry_db=db).model_id == mid
        assert lookup(mid, registry_db=db).version == 1
    assert lookup("baseline", registry_db=db).version == baseline.version


def test_concurrent_upsert_same_rows(tmp_path):
    db = tmp_path / "registry.sqlite"
    model_file = make_file(tmp_path)

    first = register("m", stored_path=model_file, registry_db=db)

    # 200 concurrent idempotent re-registers of the SAME (model_id, version)
    # must not error and must collapse to a single row.
    def reregister(_):
        return register("m", version=1, stored_path=model_file, registry_db=db)

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(reregister, range(200)))

    rows = list_models(include_deleted=True, registry_db=db)
    assert len(rows) == 1
    assert rows[0].created_at_utc == first.created_at_utc
    assert all(r.version == 1 for r in records)
