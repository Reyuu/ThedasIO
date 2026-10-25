"""Real-Blender proof: a dropped import frees the datablocks it created."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from blender_runner import run

pytestmark = pytest.mark.blender

DATA = Path(__file__).resolve().parent.parent / "data"


def _asset_dir(tmp_path: Path) -> Path:
    # c_corspidra_0 renders its MAO, so the import builds image datablocks too
    work = tmp_path / "assets"
    work.mkdir()
    for src, name in (
        ("c_corspidra_0.mmh", "c_corspidra_0.mmh"),
        ("c_corspidr_0.msh", "c_corspidr_0.msh"),
        ("pf_arm_hvyc.mao", "c_corspidra_0.mao"),
        ("pf_arm_hvyc.mao", "c_corspidr_0.mao"),
    ):
        shutil.copy(DATA / src, work / name)
    return work


def test_deleted_import_frees_datablocks(tmp_path):
    work = str(_asset_dir(tmp_path)).replace("\\", "/")
    script = textwrap.dedent("""\
    import os, sys
    import bpy
    def counts():
        def tagged(coll):
            return sum(
                1
                for b in coll
                if getattr(b, "users", 1) == 0 and b.get("thedas_io")
            )
        return {
            "images": len(bpy.data.images),
            "materials": len(bpy.data.materials),
            "meshes": len(bpy.data.meshes),
            "armatures": len(bpy.data.armatures),
            "node_groups": len(bpy.data.node_groups),
            "orphans": tagged(bpy.data.images)
            + tagged(bpy.data.materials)
            + tagged(bpy.data.meshes)
            + tagged(bpy.data.armatures)
            + tagged(bpy.data.node_groups),
        }

    def main():
        sys.path.insert(0, "src")
        import bpy
        import thedas_io as ext
        from thedas_io import ui
        try:
            ext.unregister()
        except Exception:
            pass
        ext.register()

        user_image = bpy.data.images.new("user_pixels", 8, 8, alpha=True)
        user_material = bpy.data.materials.new("user_material")
        user_mesh = bpy.data.meshes.new("user_mesh")

        sc = bpy.context.scene
        sc.thedas_io_disk_file = os.path.join("@WORK@", "c_corspidra_0.mmh")
        sc.thedas_io_disk_local = True
        sc.thedas_io_weld = True
        try:
            bpy.ops.thedas_io.import_file()
            after_import = counts()
            for obj in list(sc.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            after_delete = counts()
            removed = ui._sweep_orphans()
            after_sweep = counts()
        finally:
            ext.unregister()

        return {
            "after_import": after_import,
            "after_delete": after_delete,
            "removed": removed,
            "after_sweep": after_sweep,
            "user_ok": [
                user_image.name in bpy.data.images,
                user_material.name in bpy.data.materials,
                user_mesh.name in bpy.data.meshes,
            ],
        }
    """).replace("@WORK@", work)
    got = run(script)

    before = got["after_import"]
    assert before["images"] > 1 and before["meshes"] > 0
    # deleting objects alone frees nothing: the addon's datablocks sit at 0 users
    assert got["after_delete"]["orphans"] > 0
    assert got["after_delete"]["images"] == before["images"]

    after = got["after_sweep"]
    assert got["removed"] > 0
    assert after["orphans"] == 0
    assert after["images"] < before["images"]
    assert after["materials"] < before["materials"]
    assert after["meshes"] < before["meshes"]
    # user datablocks are never tagged, so the sweep must leave them alone
    assert got["user_ok"] == [True, True, True]
