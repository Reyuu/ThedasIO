"""Vertex-colour (usage 10) decode/encode: the DA2 character mask.

DA2 character meshes store a baked per-vertex mask in a D3DCOLOR declarator.
Import keeps it in a Blender attribute and export writes it back, so a topology
change no longer has to be refused for these chunks.
"""

from __future__ import annotations

import struct

from thedas_io.export import (
    ITEMS,  # noqa: F401 - imported for the registry fixture's sake
    _chunk_edited,
    _color_values,
    _encode_fresh,
    _encode_row,
    _modelled_usages,
    _unmodelled_usages,
)
from thedas_io.geometry import COLOR_ATTRIBUTE, decode_chunk
from thedas_io.msh.description import Mesh

POS = {8026: 0, 8027: 0, 8028: 2, 8029: 0, 8030: 0, 8031: 0}  # FLOAT3 POSITION
COLOR = {8026: 0, 8027: 12, 8028: 4, 8029: 10, 8030: 0, 8031: 0}  # D3DCOLOR


def _mesh(declarators, stride, blob, verts):
    chunk = {
        2: "c",
        8000: stride,
        8001: verts,
        8002: 0,
        8003: 0,
        8006: 0,
        8008: verts,
        8009: 0,
        8025: [dict(d) for d in declarators],
    }
    return Mesh({2: "m", 8021: [chunk], 8022: list(blob), 8023: []})


def _two_rows():
    blob = bytearray()
    blob += struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<4B", 0, 0, 0, 255)
    blob += struct.pack("<3f", 1.0, 2.0, 3.0) + struct.pack("<4B", 10, 20, 30, 255)
    return _mesh([POS, COLOR], 16, blob, 2)


def test_bgra_bytes_decode_to_rgba_floats():
    build = decode_chunk(_two_rows(), _two_rows().chunks[0])

    assert build["colors"][0] == (0.0, 0.0, 0.0, 1.0)
    # stored B=10 G=20 R=30 A=255 becomes R=30 G=20 B=10 A=255
    assert build["colors"][1][0] == 30 / 255
    assert build["colors"][1][1] == 20 / 255
    assert build["colors"][1][2] == 10 / 255
    assert build["colors"][1][3] == 1.0
    assert len(build["colors"]) == build["vert_count"]


def test_colour_counts_as_modelled_for_the_types_we_can_round_trip():
    assert _modelled_usages() >= {10}
    assert _unmodelled_usages(_two_rows().chunks[0]) == []

    # a half2 cell is not a colour layout the exporter writes
    odd = {**COLOR, 8028: 15, 8027: 0}
    assert _unmodelled_usages(_mesh([odd], 16, b"\x00" * 32, 2).chunks[0]) == [10]


def test_encode_row_leaves_an_untouched_mask_byte_identical():
    mesh = _two_rows()
    declarator = mesh.chunks[0].declarators[1]
    obj = type("O", (), {"name": "chunk"})()

    state = {"colors": [(0.0, 0.0, 0.0, 1.0)]}
    assert _encode_row(declarator, state, [{}], [0, 0, 0, 255], 0, obj) is None

    state = {"colors": [(1.0, 0.5, 0.25, 1.0)]}
    assert _encode_row(declarator, state, [{}], [0, 0, 0, 255], 0, obj) == [64, 128, 255, 255]


def test_encode_row_keeps_the_source_when_the_attribute_is_gone():
    mesh = _two_rows()
    declarator = mesh.chunks[0].declarators[1]
    obj = type("O", (), {"name": "chunk"})()

    assert _encode_row(declarator, {"colors": None}, [{}], [0, 0, 0, 255], 0, obj) is None
    assert _encode_row(declarator, {}, [{}], [0, 0, 0, 255], 0, obj) is None


def test_encode_fresh_writes_bgra_from_the_live_attribute():
    mesh = _two_rows()
    declarator = mesh.chunks[0].declarators[1]
    obj = type("O", (), {"name": "chunk"})()

    rec = {"color": (1.0, 0.5, 0.25, 1.0), "pos": (0, 0, 0), "uvs": [], "normal": None}
    assert _encode_fresh(declarator, rec, None, 0, obj) == [64, 128, 255, 255]
    assert _encode_fresh(declarator, dict(rec, color=None), None, 0, obj) is None


def test_color_values_write_the_files_own_layout():
    assert _color_values((1.0, 0.5, 0.25, 1.0), 4) == [64, 128, 255, 255]
    assert _color_values((2.0, -1.0, 0.0, 1.0), 4) == [0, 0, 255, 255]
    # float colour columns are shader data: no clamp, no channel swap, 3-comps
    assert _color_values((0.25, -72.5, 2.0, 0.5), 16) == [0.25, -72.5, 2.0, 0.5]
    assert _color_values((0.25, 0.5, 0.75, 1.0), 2) == [0.25, 0.5, 0.75]
    # 8-bit source values survive the float round trip exactly
    for byte in range(256):
        rgba = (byte / 255, byte / 255, byte / 255, 1.0)
        assert _color_values(rgba, 4)[2] == byte


def test_a_repainted_mask_marks_the_chunk_edited():
    decoded = {
        "vert_count": 1,
        "positions": [(0.0, 0.0, 0.0)],
        "triangles": [],
        "uvs": {},
        "normals": [],
        "colors": [(0.0, 0.0, 0.0, 1.0)],
        "groups": {},
    }
    state = {
        "positions": [(0.0, 0.0, 0.0)],
        "triangles": [],
        "uvs": [],
        "normals": None,
        "weights": [{}],
        "colors": [(0.0, 0.0, 0.0, 1.0)],
    }
    obj = type("O", (), {"name": "chunk"})()

    assert _chunk_edited(decoded, state, obj) is False
    state["colors"] = [(1.0, 1.0, 1.0, 1.0)]
    assert _chunk_edited(decoded, state, obj) is True


def test_the_attribute_name_is_stable():
    # the export looks the layer up by this name; a rename silently drops edits
    assert COLOR_ATTRIBUTE == "thedas_io_color"
