"""Export operator end to end, headless: real MSH + synthetic MAO/textures.

Covers what the pure unit tests cannot: chunk -> MAO resolution via live
materials, the OBJ/MTL/texture/meshmap files landing together, and usemtl
names matching the texture directories Painter will turn into sets.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from conftest import DATA_DIR
from thedas_io import ui
from thedas_io.export import ITEMS, reset
from thedas_io.materials import parse_mao
from thedas_io.msh.description import MSHReader


def _dds_bytes(color):
    from PIL import Image

    img = Image.new("RGBA", (8, 8), color)
    buf = io.BytesIO()
    img.save(buf, "DDS")
    return buf.getvalue()


MAO_TEXT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<MaterialObject Name="TEST_MAT">
\t<Material Name="Armour.mat"></Material>
\t<DefaultSemantic Name="NoTint"></DefaultSemantic>
\t<Texture Name="mml_tDiffuse" ResName="t_0d.dds"></Texture>
\t<Texture Name="mml_tNormalMap" ResName="t_0n.dds"></Texture>
\t<Texture Name="mml_tSpecularMask" ResName="t_0s.dds"></Texture>
</MaterialObject>"""


def _populate():
    # One mesh chunk + MAO + textures, as import would register them.
    from thedas_io.export import remember

    reset()
    raw = (DATA_DIR / "c_corspidr_0.msh").read_bytes()
    mesh = MSHReader(raw).mesh
    chunk = mesh.chunks[0]
    mat = SimpleNamespace(name="TEST_MAT")
    obj = SimpleNamespace(data=SimpleNamespace(materials=[mat]))
    remember("c_corspidr_0.msh", "msh", mesh, raw)["objects"].append((obj, chunk))
    remember("TEST_MAT", "mao", parse_mao(MAO_TEXT), MAO_TEXT.encode("utf-8"))
    remember("t_0d.dds", "texture", None, _dds_bytes((200, 100, 50, 255)))
    remember("t_0n.dds", "texture", None, _dds_bytes((128, 128, 128, 128)))
    remember("t_0s.dds", "texture", None, _dds_bytes((40, 30, 20, 200)))


def _context(dest: Path):
    scene = SimpleNamespace(thedas_io_sp_dir=str(dest), thedas_io_sp_split_packed=True)
    return SimpleNamespace(scene=scene)


def test_export_writes_obj_mtl_textures_meshmaps(tmp_path):
    _populate()
    op = ui.THEDAS_IO_OT_export_substance()
    op.directory = str(tmp_path / "sp")
    assert op.execute(_context(tmp_path / "sp")) == {"FINISHED"}

    dest = tmp_path / "sp"
    obj = (dest / "model.obj").read_text(encoding="utf-8")
    assert "mtllib model.mtl" in obj
    assert "usemtl TEST_MAT" in obj
    # UVs pass through as decoded (Blender convention, V=0 at the bottom):
    # the first decoded UV is (0.344, 0.0752), and flipping it here would
    # mirror every texture vertically in Painter.
    assert "vt 0.343994 0.0751953" in obj
    mtl = (dest / "model.mtl").read_text(encoding="utf-8")
    assert "newmtl TEST_MAT" in mtl
    assert "map_Kd textures/TEST_MAT/basecolor.png" in mtl
    assert "map_Bump textures/TEST_MAT/normal.png" in mtl
    tex = dest / "textures" / "TEST_MAT"
    assert {p.name for p in tex.glob("*.png")} == {
        "basecolor.png",
        "normal.png",
        "specular.png",
        "glossiness.png",
    }
    meshmap = dest / "meshmaps" / "TEST_MAT_normal_base.png"
    assert meshmap.is_file()
    # the mesh-map copy is the normal SP will auto-assign at project creation
    from PIL import Image

    assert Image.open(meshmap).tobytes() == Image.open(tex / "normal.png").tobytes()
    # cross-file consistency: every usemtl resolves to a newmtl, the mtllib
    # file exists next to the OBJ, and every map_ target exists on disk.
    # SP's "failed to locate material, creating a new one" means exactly
    # this lookup failed, so it is pinned here, not left to manual testing.
    import re

    usemtls = [l.split(None, 1)[1] for l in obj.splitlines() if l.startswith("usemtl ")]
    mtllibs = [l.split(None, 1)[1] for l in obj.splitlines() if l.startswith("mtllib ")]
    assert mtllibs and all((dest / m).is_file() for m in mtllibs)
    mtl_all = "".join((dest / m).read_text(encoding="utf-8") for m in mtllibs)
    newmtls = [l.split(None, 1)[1] for l in mtl_all.splitlines() if l.startswith("newmtl ")]
    assert usemtls and all(u in newmtls for u in usemtls)
    maps = re.findall(r"^map_\w+\s+(\S+)", mtl_all, re.MULTILINE)
    assert maps and all((dest / m).is_file() for m in maps)


def test_export_reports_but_skips_missing_texture(tmp_path):
    _populate()
    ITEMS.pop("t_0n.dds")  # normal never imported
    op = ui.THEDAS_IO_OT_export_substance()
    op.directory = str(tmp_path / "sp")
    assert op.execute(_context(tmp_path / "sp")) == {"FINISHED"}
    assert not (tmp_path / "sp" / "textures" / "TEST_MAT" / "normal.png").exists()
    assert (tmp_path / "sp" / "textures" / "TEST_MAT" / "basecolor.png").exists()
    assert any("t_0n.dds" in text for _, text in op.reports)


class _FakePixels:
    def __init__(self):
        self.writes = []

    def foreach_set(self, flat):
        self.writes.append(len(flat))


class _FakeImage:
    def __init__(self, name, size):
        self.name = name
        self.size = list(size)
        self.pixels = _FakePixels()
        self.scaled = []
        self.packed = 0

    def scale(self, w, h):
        self.scaled.append((w, h))
        self.size = [w, h]

    def update(self):
        pass

    def pack(self):
        self.packed += 1


def _sp_flat_files(dest: Path, size: int):
    # SP output as the template writes it: flat $textureSet_$map PNGs.
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(21)
    for stem in ("basecolor", "normal", "specular"):
        Image.fromarray(rng.integers(0, 256, (size, size, 3)).astype("uint8")).save(
            dest / "textures" / f"TEST_MAT_{stem}.png"
        )


def _run_import(tmp_path, keep: bool):
    # Run the import operator headless; returns (result, images, cache).
    _populate()
    dest = tmp_path / "sp"
    (dest / "textures").mkdir(parents=True)
    _sp_flat_files(dest, 16)
    images = [_FakeImage(n, (8, 8)) for n in ("t_0d.dds", "t_0n.dds", "t_0s.dds")]
    missing = object()
    saved = [
        (fake_bpy.bpy.data, "images", getattr(fake_bpy.bpy.data, "images", missing)),
        (fake_bpy.bpy.data, "filepath", getattr(fake_bpy.bpy.data, "filepath", missing)),
        (fake_bpy.bpy.app, "tempdir", getattr(fake_bpy.bpy.app, "tempdir", missing)),
    ]
    fake_bpy.bpy.data.images = images
    fake_bpy.bpy.data.filepath = ""
    fake_bpy.bpy.app.tempdir = str(tmp_path / "btemp")
    try:
        scene = SimpleNamespace(
            thedas_io_sp_dir=str(dest),
            thedas_io_sp_split_packed=True,
            thedas_io_sp_keep_resolution=keep,
        )
        op = ui.THEDAS_IO_OT_import_substance()
        op.directory = ""
        result = op.execute(SimpleNamespace(scene=scene))
    finally:
        for obj, attr, value in saved:
            if value is missing:
                try:
                    delattr(obj, attr)
                except AttributeError:
                    pass
            else:
                setattr(obj, attr, value)
    cache = tmp_path / "btemp" / "thedas_io_textures"
    return result, images, cache


def _cache_size(cache: Path, name: str):
    from PIL import Image

    with Image.open(cache / name) as im:
        return tuple(im.size)


def test_import_downscales_to_source_by_default(tmp_path):
    result, images, cache = _run_import(tmp_path, keep=False)
    assert result == {"FINISHED"}
    assert _cache_size(cache, "t_0d.dds") == (8, 8)
    assert _cache_size(cache, "t_0n.dds") == (8, 8)
    assert _cache_size(cache, "t_0s.dds") == (8, 8)
    # nothing rescaled: repacked pixels already meet the live images
    assert all(img.scaled == [] for img in images)
    assert all(img.packed >= 1 for img in images)


def test_import_keep_resolution_skips_downscale(tmp_path):
    result, images, cache = _run_import(tmp_path, keep=True)
    assert result == {"FINISHED"}
    assert _cache_size(cache, "t_0d.dds") == (16, 16)
    assert _cache_size(cache, "t_0n.dds") == (16, 16)
    assert _cache_size(cache, "t_0s.dds") == (16, 16)
    # live images were rescaled before reload, so no size mismatch stops it
    assert all(img.size == [16, 16] for img in images)
    assert all(img.scaled == [(16, 16)] for img in images)
    assert all(img.packed >= 1 for img in images)
