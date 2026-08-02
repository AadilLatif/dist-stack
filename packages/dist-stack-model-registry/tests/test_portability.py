"""Path portability (§8 item 7).

``store_relative_to_db=True`` stores paths relative to the DB's parent; moving
the DB together with the model files keeps lookups working. This is the
property that motivated relative storage.
"""
from __future__ import annotations

import os
import shutil

from dist_stack import lookup, register


def test_relative_storage_moves_with_db(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    model = src / "model.json"
    model.write_text("{}")
    db = src / "registry.sqlite"

    rec = register("m", stored_path=model, registry_db=db,
                   store_relative_to_db=True)
    assert rec.stored_path == "model.json"  # relative, not absolute
    assert lookup("m", registry_db=db, resolve_path=False).stored_path == "model.json"

    # Move the whole store (DB + model file) to a new directory.
    new_db = dst / "registry.sqlite"
    shutil.move(str(db), str(new_db))
    shutil.move(str(model), str(dst / "model.json"))

    # resolve_path=True (default) now points at the new location.
    rec2 = lookup("m", registry_db=new_db)
    assert rec2.stored_path == str(dst / "model.json")
    assert os.path.exists(rec2.stored_path)
    # resolve_path=False keeps returning the stored relative value verbatim.
    assert lookup("m", registry_db=new_db, resolve_path=False).stored_path == "model.json"


def test_absolute_storage_does_not_move(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    model = src / "model.json"
    model.write_text("{}")
    db = src / "registry.sqlite"

    rec = register("m", stored_path=model, registry_db=db)  # default: absolute
    assert rec.stored_path == os.path.abspath(str(model))

    new_db = dst / "registry.sqlite"
    shutil.move(str(db), str(new_db))
    shutil.move(str(model), str(dst / "model.json"))

    # The stored absolute path is unchanged and now stale.
    rec2 = lookup("m", registry_db=new_db)
    assert rec2.stored_path == os.path.abspath(str(model))
    assert not os.path.exists(rec2.stored_path)
