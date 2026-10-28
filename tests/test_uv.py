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
