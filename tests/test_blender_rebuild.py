"""Real-Blender proof: the registry rebuilds from the scene it is given.

`rebuild_registry(scene)` used to read `bpy.context.scene.objects`, so on file
load every scene re-bound to the active scene's meshes: an import living in a
second scene came back with no objects, and its export silently had nothing to
compare against.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from blender_runner import run

pytestmark = pytest.mark.blender

DATA = Path(__file__).resolve().parent.parent / "data"


def test_rebuild_registry_binds_the_scene_it_is_given(tmp_path):
    work = tmp_path / "assets"
    work.mkdir()
    shutil.copy(DATA / "c_corspidr_0.msh", work / "c_corspidr_0.msh")
    db = tmp_path / "assets.sqlite"

    from thedas_io.asset_db.scanner import scan

    scan([str(work)], str(db))

    script = (
        textwrap.dedent("""\
    import os, sys
    def main():
        sys.path.insert(0, "src")
        import bpy
        import thedas_io as ext
        from thedas_io import ui
        from thedas_io.export import ITEMS, reset
        try:
            ext.unregister()
        except Exception:
            pass
        ext.register()

        sc = bpy.context.scene
        sc.thedas_io_db_path = "@DB@"
        sc.thedas_io_disk_file = os.path.join("@WORK@", "c_corspidr_0.msh")
        sc.thedas_io_disk_local = True
        try:
            bpy.ops.thedas_io.import_file()
            moved = [o.name for o in sc.collection.objects]

            # move the import to a scene that is not the active one
            other = bpy.data.scenes.new("other")
            other.thedas_io_db_path = "@DB@"
            other.thedas_io_registry = sc.thedas_io_registry
            for obj in list(sc.collection.objects):
                other.collection.objects.link(obj)
                sc.collection.objects.unlink(obj)

            reset()
            notes = ui.rebuild_registry(other)
            item = ITEMS.get("c_corspidr_0.msh") or {}
            payload = {
                "notes": notes,
                "model": item.get("model") is not None,
                "pairs": len(item.get("objects") or []),
                "pair_names": [e[0].name for e in item.get("objects") or []],
            }
        finally:
            ext.unregister()

        return {
            "moved": moved,
            "active_scene_objects": len(sc.collection.objects),
            "other_objects": len(other.collection.objects),
            "payload": payload,
        }
    """)
        .replace("@DB@", str(db).replace("\\", "/"))
        .replace("@WORK@", str(work).replace("\\", "/"))
    )
    got = run(script)

    assert got["active_scene_objects"] == 0
    assert got["other_objects"] > 0
    payload = got["payload"]
    assert payload["notes"] == []
    assert payload["model"] is True
    assert payload["pairs"] == 1
    assert payload["pair_names"] == got["moved"]
