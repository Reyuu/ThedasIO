"""Weld split-back: row<->vert map validation and per-row loop expansion.

Covers thedas_io.export._expand_rows / _row_map headless with
hand-built mesh doubles: a 2-triangle quad whose 6 MSH rows weld to 4
Blender verts, with a UV seam across the twins (rows 0 and 3 share a
position but carry different UVs) so a first-loop-only read would fail
visibly.
"""

from __future__ import annotations

import sys

import pytest

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from thedas_io.export import _expand_rows, _row_map

# MSH rows:           r0        r1        r2        r3(twin r0) r4(twin r2) r5
_POSITIONS = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
]
_ROW_TO_VERT = [0, 1, 2, 0, 2, 3]
_DTRIS = [(0, 1, 2), (3, 4, 5)]
# seam: twin rows disagree on UV, like a real UV-seam split
_UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.75, 0.75), (0.0, 1.0)]
_NORMS = [
    (0.0, 0.0, 1.0),
    (0.1, 0.0, 1.0),
    (0.0, 0.1, 1.0),
    (0.2, 0.0, 1.0),
    (0.0, 0.2, 1.0),
    (0.3, 0.0, 1.0),
]


class _Loop:
    def __init__(self, vertex_index):
        self.vertex_index = vertex_index


class _Poly:
    def __init__(self, vertices, loop_start):
        self.vertices = vertices
        self.loop_start = loop_start


class _UV:
    def __init__(self, uv):
        self.uv = uv


class _Layer:
    def __init__(self, uvs):
        self.data = [_UV(uv) for uv in uvs]


class _Corner:
    def __init__(self, vector):
        self.vector = vector


class _Vert:
    def __init__(self, co, index):
        self.co = co
        self.index = index
        self.groups = []


class _Data:
    def __init__(self, n_verts, loop_verts, loop_uvs, loop_normals=()):
        self.vertices = [_Vert((float(i), 0.0, 0.0), i) for i in range(n_verts)]
        self.loops = [_Loop(v) for v in loop_verts]
        self.polygons = []
        for f, start in enumerate(range(0, len(loop_verts), 3)):
            self.polygons.append(_Poly(tuple(loop_verts[start : start + 3]), start))
        self.uv_layers = [_Layer(loop_uvs)]
        if loop_normals:
            self.corner_normals = [_Corner(v) for v in loop_normals]


class _Obj:
    def __init__(self, data, props=None):
        self.name = "T"
        self._props = props or {}
        self.data = data
        self.modifiers = []
        self.vertex_groups = []

    def get(self, key, default=None):
        return self._props.get(key, default)


def _decoded(n_normals=True):
    return {
        "vert_count": 6,
        "positions": list(_POSITIONS),
        "triangles": [tuple(t) for t in _DTRIS],
        "uvs": {0: list(_UVS)},
        "normals": list(_NORMS) if n_normals else [],
        "groups": {},
    }


def _blender_state():
    # What _blender_state returns for the welded quad (4 Blender verts).
    return {
        "positions": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        "triangles": [(0, 1, 2), (0, 2, 3)],
        "uvs": [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]],
        "normals": [(0.0, 0.0, 1.0)] * 4,
        "weights": [{7: 0.5}, {}, {7: 0.25}, {}],
    }


def _obj(row_to_vert=_ROW_TO_VERT):
    # loop order follows the Blender faces; loop UVs/normals carry the
    # ORIGINAL row values, exactly as build_chunk_object expands them
    loop_rows = [0, 1, 2, 3, 4, 5]
    data = _Data(
        4, [0, 1, 2, 0, 2, 3], [_UVS[r] for r in loop_rows], [_NORMS[r] for r in loop_rows]
    )
    return _Obj(data, {"thedas_io_row_to_vert": list(row_to_vert)})


def test_row_map_absent_is_legacy():
    obj = _Obj(_Data(6, [0, 1, 2, 3, 4, 5], _UVS), {})
    assert _row_map(obj, 6, 6) is None
    with pytest.raises(ValueError, match="vertex count changed"):
        _row_map(obj, 6, 5)


def test_row_map_rejects_stale():
    obj = _Obj(_Data(4, [], []), {"thedas_io_row_to_vert": [0, 1]})
    with pytest.raises(ValueError, match="weld map stale"):
        _row_map(obj, 6, 4)
    obj = _Obj(_Data(4, [], []), {"thedas_io_row_to_vert": [0, 1, 2, 0, 2, 9]})
    with pytest.raises(ValueError, match="weld map stale"):
        _row_map(obj, 6, 4)
    assert _row_map(_obj(), 6, 4) == _ROW_TO_VERT


def test_expand_legacy_returns_state_verbatim():
    decoded = _decoded()
    obj = _Obj(_Data(6, [0, 1, 2, 3, 4, 5], _UVS), {})
    bstate = _blender_state()
    bstate["positions"] = list(_POSITIONS)
    bstate["triangles"] = [tuple(t) for t in _DTRIS]
    assert _expand_rows(decoded, obj, bstate) is bstate


def test_expand_welded_splits_seams():
    decoded = _decoded()
    state = _expand_rows(decoded, _obj(), _blender_state())
    assert len(state["positions"]) == 6
    assert state["triangles"] == [(0, 1, 2), (3, 4, 5)]
    # positions and weights fan out through the map
    assert state["positions"][0] == state["positions"][3] == (0.0, 0.0, 0.0)
    assert state["weights"][0] == state["weights"][3] == {7: 0.5}
    assert state["weights"][2] == state["weights"][4] == {7: 0.25}
    # ...but seam twins keep their own loop UVs and normals, not the
    # first loop's values a per-vertex collapse would give both
    assert state["uvs"][0][0] == (0.0, 0.0)
    assert state["uvs"][0][3] == (0.5, 0.5)
    assert state["uvs"][0][2] == (1.0, 1.0)
    assert state["uvs"][0][4] == (0.75, 0.75)
    assert state["normals"][0] == (0.0, 0.0, 1.0)
    assert state["normals"][3] == (0.2, 0.0, 1.0)


def test_expand_topology_mismatch_raises():
    decoded = _decoded()
    obj = _obj()
    bstate = _blender_state()
    bstate["triangles"] = [(0, 2, 1), (0, 2, 3)]  # user flipped a winding
    with pytest.raises(ValueError, match="face topology changed"):
        _expand_rows(decoded, obj, bstate)


def test_expand_face_count_mismatch_raises():
    decoded = _decoded()
    obj = _obj()
    bstate = _blender_state()
    bstate["triangles"] = [(0, 1, 2)]
    with pytest.raises(ValueError, match="face count changed"):
        _expand_rows(decoded, obj, bstate)


def test_expand_orphan_row_keeps_decoded():
    # A row no face references cannot have been edited in the viewport.
    decoded = _decoded()
    decoded["vert_count"] = 7
    decoded["positions"] = list(_POSITIONS) + [(9.0, 9.0, 9.0)]
    decoded["uvs"] = {0: list(_UVS) + [(0.9, 0.9)]}
    decoded["normals"] = list(_NORMS) + [(0.0, 0.0, 1.0)]
    obj = _Obj(
        _Data(
            4,
            [0, 1, 2, 0, 2, 3],
            [_UVS[r] for r in (0, 1, 2, 3, 4, 5)],
            [_NORMS[r] for r in (0, 1, 2, 3, 4, 5)],
        ),
        {"thedas_io_row_to_vert": _ROW_TO_VERT + [1]},
    )
    state = _expand_rows(decoded, obj, _blender_state())
    assert len(state["positions"]) == 7
    assert state["uvs"][0][6] == (0.9, 0.9)
    assert state["normals"][6] == (0.0, 0.0, 1.0)


def test_expand_without_normals_stays_none():
    decoded = _decoded(n_normals=False)
    data = _Data(4, [0, 1, 2, 0, 2, 3], [_UVS[r] for r in (0, 1, 2, 3, 4, 5)])
    assert not hasattr(data, "corner_normals")
    state = _expand_rows(
        decoded, _Obj(data, {"thedas_io_row_to_vert": list(_ROW_TO_VERT)}), _blender_state()
    )
    assert state["normals"] is None
