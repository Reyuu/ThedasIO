"""Texcoord declarators that carry more than one texture coordinate set.

DA2 packs a second UV set into a 4-value texcoord cell, and DAO stores a third
column on some water/lava meshes. Reading only the first pair dropped data, and
a rebuild wrote zeros over the rest.
"""

from __future__ import annotations

import struct

import pytest

from thedas_io.export import _encode_fresh, _encode_row, _rebuild_chunk, _uv_slots
from thedas_io.geometry import decl_components, decode_chunk, uv_pairs
from thedas_io.msh.description import Mesh

POS = {8026: 0, 8027: 0, 8028: 2, 8029: 0, 8030: 0}  # FLOAT3 POSITION
UV4 = {8026: 0, 8027: 12, 8028: 16, 8029: 5, 8030: 0}  # FLOAT16_4 TEXCOORD
UV3 = {8026: 0, 8027: 12, 8028: 2, 8029: 5, 8030: 0}  # FLOAT3 TEXCOORD


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


def _two_sets():
    blob = struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<4e", 0.0, 0.25, 0.5, 0.75)
    return _mesh([POS, UV4], 20, blob, 1)


def _three_columns():
    blob = struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<3f", 0.25, 0.5, 0.125)
    return _mesh([POS, UV3], 24, blob, 1)


def test_a_four_value_texcoord_decodes_as_two_uv_layers():
    mesh = _two_sets()
    uvs = decode_chunk(mesh, mesh.chunks[0])["uvs"]

    assert sorted(uvs) == [0, 1]
    assert uvs[0] == [(0.0, 0.75)]  # V flipped on import
    assert uvs[1] == [(0.5, 0.25)]


def test_uv_pairs_reports_the_layer_and_component_offset():
    mesh = _two_sets()
    slots = uv_pairs(mesh.chunks[0])
    assert [(layer, offset) for layer, _d, offset in slots] == [(0, 0), (1, 2)]

    three = _three_columns()
    assert [(layer, offset) for layer, _d, offset in uv_pairs(three.chunks[0])] == [(0, 0)]
    assert decl_components(2) == 3


def test_a_three_column_texcoord_keeps_its_extra_cell_on_patch():
    mesh = _three_columns()
    chunk = mesh.chunks[0]
    declarator = chunk.declarators[1]
    obj = type("O", (), {"name": "chunk"})()

    state = {"uvs": [[(0.1, 0.2)]]}
    got = _encode_row(declarator, state, [{}], [0.25, 0.5, 0.125], 0, obj, _uv_slots(chunk))
    assert got == pytest.approx([0.1, 0.8, 0.125])


def test_a_rebuild_writes_every_uv_pair_back():
    mesh = _two_sets()
    chunk = mesh.chunks[0]
    declarator = chunk.declarators[1]
    obj = type("O", (), {"name": "chunk"})()

    rec = {"uvs": [(0.1, 0.2), (0.3, 0.4)], "pos": (0, 0, 0), "normal": None, "weights": {}}
    got = _encode_fresh(declarator, rec, None, 0, obj, _uv_slots(chunk))
    assert got == pytest.approx([0.1, 0.8, 0.3, 0.6])


def test_a_rebuild_refuses_a_texcoord_it_cannot_fill():
    mesh = _three_columns()
    obj = type("O", (), {"name": "chunk"})()
    with pytest.raises(ValueError, match="stores 3 values"):
        _rebuild_chunk(mesh.chunks[0], obj, {})


# --- DA2 tangent frames stored in texcoord slots -----------------------------

FRAME_POS = POS
FRAME_N = {8026: 0, 8027: 12, 8028: 8, 8029: 5, 8030: 0}
FRAME_T = {8026: 0, 8027: 16, 8028: 8, 8029: 5, 8030: 1}
FRAME_B = {8026: 0, 8027: 20, 8028: 8, 8029: 5, 8030: 3}
FRAME_UV = {8026: 0, 8027: 24, 8028: 16, 8029: 5, 8030: 2}


def _frame_chunk(normal=(127, 127, 255, 255), tangent=(255, 127, 127, 0), binormal=(0, 0, 0, 0)):
    blob = struct.pack("<3f", 0.0, 0.0, 0.0)
    blob += struct.pack("<4B", *normal)
    blob += struct.pack("<4B", *tangent)
    blob += struct.pack("<4B", *binormal)
    blob += struct.pack("<4e", 0.0, 0.25, 0.5, 0.75)
    return _mesh([FRAME_POS, FRAME_N, FRAME_T, FRAME_B, FRAME_UV], 32, blob, 1)


def _blob_of(mesh):
    from thedas_io.geometry import chunk_blob

    return chunk_blob(mesh, mesh.chunks[0])


def test_byte_texcoords_that_are_a_direction_are_split_off_as_frames():
    from thedas_io.geometry import split_texcoords

    mesh = _frame_chunk()
    blob = _blob_of(mesh)
    real, frames = split_texcoords(mesh.chunks[0], blob)

    assert len(frames) == 3
    assert [layer for layer, _d, _o in real] == [0, 1]  # the half4 pair only
    build = decode_chunk(mesh, mesh.chunks[0])
    assert sorted(build["uvs"]) == [0, 1]
    assert [index for index, _values in build["frames"]] == [0, 1, 3]
    # (127, 127, 255) is +Z in the biased space
    assert build["frames"][0][1][0] == pytest.approx((0.0, 0.0, 1.0, 1.0), abs=0.01)


def test_byte_texcoords_that_are_not_directions_stay_uu_layers():
    from thedas_io.geometry import split_texcoords

    mesh = _frame_chunk(normal=(10, 200, 30, 255), tangent=(200, 10, 40, 0))
    real, frames = split_texcoords(mesh.chunks[0], _blob_of(mesh))
    assert frames == []
    # three byte channels of two pairs each, then the half4 pair
    assert [layer for layer, _d, _o in real] == list(range(8))


def test_frame_names_are_user_facing():
    from thedas_io.geometry import frame_name

    assert frame_name(0) == "Frame Normal"
    assert frame_name(1) == "Frame Tangent"
    assert frame_name(3) == "Frame Binormal"
    assert frame_name(7) == "Frame 7"


def test_frame_bytes_round_trip_every_byte():
    from thedas_io.export import _frame_bytes

    for byte in range(256):
        biased = (byte - 128) / 127
        assert _frame_bytes((biased, biased, biased, 1.0))[2] == byte
    # the sign column keeps the 0/255 the shipped files use
    assert _frame_bytes((0.0, 0.0, 1.0, -1.0))[3] == 0
    assert _frame_bytes((0.0, 0.0, 1.0, 1.0))[3] == 255


def test_frames_are_regenerated_when_the_switch_is_on():
    mesh = _frame_chunk()
    chunk = mesh.chunks[0]
    declarator = chunk.declarators[1]  # the normal channel
    obj = type("O", (), {"name": "chunk"})()
    state = {
        "normals": [(0.0, 1.0, 0.0)],
        "tangent_loops": {},
        "frames": {0: [(-1.0, 0.0, 0.0, 1.0)]},
    }

    got = _encode_row(declarator, state, [{}], [127, 127, 255, 255], 0, obj, {1: 0}, 0, True)
    assert got == [128, 255, 128, 255]  # +Y in the biased byte space


def test_frames_fall_back_to_the_attribute_when_the_switch_is_off():
    mesh = _frame_chunk()
    declarator = mesh.chunks[0].declarators[1]
    obj = type("O", (), {"name": "chunk"})()
    stored = (0.0, 0.0, 1.0, 1.0)
    state = {"normals": [(0.0, 1.0, 0.0)], "tangent_loops": {}, "frames": {0: [stored]}}

    off = _encode_row(declarator, state, [{}], [0, 0, 0, 0], 0, obj, {1: 0}, 0, False)
    assert off == [128, 128, 255, 255]
    on = _encode_row(declarator, state, [{}], [0, 0, 0, 0], 0, obj, {1: 0}, 0, True)
    assert on == [128, 255, 128, 255]


def test_a_rebuild_writes_the_frame_from_the_live_basis():
    mesh = _frame_chunk()
    chunk = mesh.chunks[0]
    declarator = chunk.declarators[2]  # the tangent channel
    obj = type("O", (), {"name": "chunk"})()

    rec = {
        "uvs": [(0.0, 0.0), (0.0, 0.0)],
        "pos": (0.0, 0.0, 0.0),
        "normal": (0.0, 0.0, 1.0),
        "weights": {},
        "frames": None,
        "vert": 0,
    }
    tloops = {0: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))}
    got = _encode_fresh(declarator, rec, tloops, 0, obj, _uv_slots(chunk), 1, True)
    assert got == [255, 128, 128, 255]  # +X, w = +1 for a right-handed basis
