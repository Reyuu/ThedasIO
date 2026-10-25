"""Real-Blender proof: a removed object reference does not crash export."""

from __future__ import annotations

import textwrap

import pytest

from blender_runner import run

pytestmark = pytest.mark.blender


def test_export_with_removed_object_no_crash(tmp_path):
    out_dir = str(tmp_path / "out").replace("\\", "/")
    script = textwrap.dedent("""\
    import os, sys
    def main():
        sys.path.insert(0, "src")
        import thedas_io as ext
        try:
            ext.unregister()
        except Exception:
            pass
        ext.register()
        import bpy
        mmh = os.path.join("data", "c_corspidra_0.mmh")
        sc = bpy.context.scene
        sc.thedas_io_disk_file = mmh
        sc.thedas_io_disk_local = True
        sc.thedas_io_weld = True
        out = "@OUT@"
        sc.thedas_io_export_dir = out
        bpy.ops.thedas_io.import_file()
        mesh_obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
        bpy.data.objects.remove(mesh_obj, do_unlink=True)
        try:
            res = sorted(bpy.ops.thedas_io.export_assets(directory=out))
        finally:
            for o in list(bpy.context.scene.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            ext.unregister()
        return {"res": res, "files": sorted(__import__("os").listdir(out))}
    """).replace("@OUT@", out_dir)
    got = run(script)
    assert "ReferenceError" not in str(got.get("res"))
    assert "c_corspidra_0.mmh" in got["files"]
