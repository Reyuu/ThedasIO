"""Check-model operator: every warning category, headless.

Synthetic chunks are built by hand (struct-packed rows + declarator rows)
so each defect is isolated: nothing here depends on game data or Blender.
"""

from __future__ import annotations

import io
import struct
import sys
from types import SimpleNamespace

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from thedas_io import ui
from thedas_io.export import check_model, reset
from thedas_io.msh.description import Mesh


def _decl(offset, dtype, usage, uidx=0):
    return {8026: 0, 8027: offset, 8028: dtype, 8029: usage, 8030: uidx, 8031: 0}


def _mesh(rows, tris, skinned=True):
    # One-chunk Mesh: rows of (pos xyz, uv, weight or None).
    blob, iblob = bytearray(), bytearray()
    decls = [_decl(0, 2, 0), _decl(12, 1, 5)]
    stride = 20
    if skinned:
        decls += [_decl(20, 3, 1), _decl(36, 5, 2)]
        stride = 40
    for pos, uv, w in rows:
        blob += struct.pack("<3f", *pos)
        blob += struct.pack("<2f", *uv)
        if skinned:
            blob += struct.pack("<4f", w, 0.0, 0.0, 0.0) if w else bytes(16)
            blob += struct.pack("<4B", 3, 0, 0, 0) if w else bytes(4)
    for tri in tris:
        iblob += struct.pack(f"<{len(tri)}H", *tri)
    chunk = {
        2: "c",
        8000: stride,
        8001: len(rows),
        8002: sum(map(len, tris)),
        8003: 0,
        8006: 0,
        8009: 0,
        8025: decls,
    }
    return Mesh({2: "m", 8021: [chunk], 8022: list(blob), 8023: list(iblob)})


def _live(mats=(), npolys=1, mods=(), nlayers=1, shapes=None):
    polys = [SimpleNamespace(vertices=(0, 1, 2)) for _ in range(npolys)]
    layers = [SimpleNamespace() for _ in range(nlayers)]
    data = SimpleNamespace(
        polygons=polys, uv_layers=layers, shape_keys=shapes, materials=list(mats)
    )
    return SimpleNamespace(data=data, modifiers=list(mods))


def _remember_msh(mesh, raw=b"raw", objects="auto", mats=()):
    from thedas_io.export import remember

    item = remember("m.msh", "msh", mesh, raw)
    if objects == "auto":
        item["objects"] = [(_live([SimpleNamespace(name=m) for m in mats]), mesh.chunks[0])]
    return item


def _remember_mao(textures=None):
    from thedas_io.export import remember

    model = {"name": "X", "textures": textures or {"a": "t.dds"}}
    return remember("m.mao", "mao", model, b"mao")


def _dds(size=(8, 8)):
    from PIL import Image

    img = Image.new("RGBA", size, (10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, "DDS")
    return buf.getvalue()


def _remember_tex(name="t.dds", raw=None):
    from thedas_io.export import remember

    return remember(name, "texture", None, raw if raw is not None else _dds())


TRI = [
    ((0.0, 0.0, 0.0), (0.0, 0.0), 0.5),
    ((1.0, 0.0, 0.0), (1.0, 0.0), 0.5),
    ((0.0, 1.0, 0.0), (0.0, 1.0), 0.5),
]


def test_clean_model_reports_nothing():
    reset()
    _remember_msh(_mesh(TRI, [(0, 1, 2)]))
    _remember_mao()
    _remember_tex()
    assert check_model() == []


def test_unweighted_row_flagged_rigid_skipped():
    reset()
    rows = [
        ((0.0, 0.0, 0.0), (0.0, 0.0), 0.5),
        ((1.0, 0.0, 0.0), (1.0, 0.0), None),
        ((0.0, 1.0, 0.0), (0.0, 1.0), 0.5),
    ]
    _remember_msh(_mesh(rows, [(0, 1, 2)]))
    notes = check_model()
    assert len(notes) == 1 and "no weights" in notes[0] and "1" in notes[0]
    # same mesh without weight declarators is rigid, never flagged
    reset()
    _remember_msh(_mesh(rows, [(0, 1, 2)], skinned=False))
    assert check_model() == []


def test_degenerate_tri_flagged():
    reset()
    rows = [
        ((0.0, 0.0, 0.0), (0.0, 0.0), 0.5),
        ((0.0, 0.0, 0.0), (0.0, 0.0), 0.5),
        ((0.0, 0.0, 0.0), (0.0, 0.0), 0.5),
    ]
    _remember_msh(_mesh(rows, [(0, 1, 2)]))
    notes = check_model()
    assert any("degenerate" in n for n in notes)


def test_missing_mao_and_texture_flagged():
    reset()
    item = _remember_msh(_mesh(TRI, [(0, 1, 2)]))
    item["objects"] = [(_live([SimpleNamespace(name="Nope")]), item["model"].chunks[0])]
    assert any("no MAO" in n for n in check_model())
    reset()
    _remember_msh(_mesh(TRI, [(0, 1, 2)]))
    _remember_mao({"a": "ghost.dds"})
    assert any("ghost.dds" in n for n in check_model())


def test_uv_out_of_range_is_warning_only():
    reset()
    rows = [
        ((0.0, 0.0, 0.0), (-0.5, 0.0), 0.5),
        ((1.0, 0.0, 0.0), (1.0, 0.0), 0.5),
        ((0.0, 1.0, 0.0), (0.0, 1.0), 0.5),
    ]
    _remember_msh(_mesh(rows, [(0, 1, 2)]))
    notes = check_model()
    assert len(notes) == 1 and "outside [0, 1]" in notes[0]


def test_live_mesh_issues_flagged():
    reset()
    item = _remember_msh(_mesh(TRI, [(0, 1, 2)]))
    obj = _live(
        mods=[SimpleNamespace(type="SUBSURF", name="Sub")], shapes=SimpleNamespace(), npolys=0
    )
    obj.data.polygons = [SimpleNamespace(vertices=(0, 1, 2, 3))]
    obj.data.uv_layers = []
    item["objects"] = [(obj, item["model"].chunks[0])]
    notes = check_model()
    text = "\n".join(notes)
    assert "non-tri" in text
    assert "Sub" in text and "not applied" in text
    assert "shape keys" in text
    assert "UV layer" in text


def test_texture_dimensions_checked():
    from thedas_io.export import _check_texture_item

    assert _check_texture_item({"file": "a.dds", "raw": _dds((8, 8))}) == []
    notes = _check_texture_item({"file": "a.dds", "raw": _dds((6, 6))})
    assert any("multiple of 4" in n for n in notes)
    notes = _check_texture_item({"file": "a.dds", "raw": _dds((12, 12))})
    assert any("power-of-two" in n for n in notes)
    assert _check_texture_item({"file": "a.dds", "raw": b"junk"}) == [
        "a.dds: unreadable image bytes"
    ]


def test_operator_reports_and_cancels_empty():
    reset()
    op = ui.THEDAS_IO_OT_check_model()
    scene = SimpleNamespace()
    assert op.execute(SimpleNamespace(scene=scene)) == {"CANCELLED"}
    reset()
    _remember_msh(_mesh(TRI, [(0, 1, 2)]))
    _remember_mao()
    _remember_tex()
    assert op.execute(SimpleNamespace(scene=scene)) == {"FINISHED"}
    assert op.reports[-1][1] == "Model clean"
    reset()
    _remember_msh(_mesh(TRI, [(0, 1, 2)]), mats=["Ghost"])  # no MAO registered
    op2 = ui.THEDAS_IO_OT_check_model()
    assert op2.execute(SimpleNamespace(scene=scene)) == {"FINISHED"}
    assert any("no MAO" in text for _, text in op2.reports)
