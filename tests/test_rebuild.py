"""Auto-retriangulation rebuild: fan helper plus full chunk rebuild.

Covers thedas_io.geometry.triangulate_loops and
thedas_io.export._rebuild_chunk headless with hand-built mesh
doubles: a Blender quad fans to two tris with per-loop UVs/normals, seam
sides split into their own rows while shared corners stay shared.
"""

from __future__ import annotations

import struct
import sys

import pytest

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from thedas_io.export import _rebuild_chunk, _TopologyChanged
from thedas_io.geometry import triangulate_loops


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


class _Decl:
    def __init__(self, usage, usage_index, dtype, offset):
        self.is_terminator = False
        self.usage = usage
        self.usage_index = usage_index
        self.dtype = dtype
        self.offset = offset


class _Data:
    def __init__(self, positions, polys, loop_uvs, loop_normals=()):
        from types import SimpleNamespace

        self.vertices = [SimpleNamespace(co=p, index=i) for i, p in enumerate(positions)]
        self.loops = [_Loop(v) for poly in polys for v in poly]
        self.polygons = []
        start = 0
        for poly in polys:
            self.polygons.append(_Poly(tuple(poly), start))
            start += len(poly)
        self.uv_layers = [_Layer(loop_uvs)]
        if loop_normals:
            self.corner_normals = [_Corner(v) for v in loop_normals]


class _Obj:
    def __init__(self, data):
        self.name = "R"
        self.data = data


def _chunk(n_uv=1):
    decls = [_Decl(0, 0, 2, 0), _Decl(3, 0, 2, 12)]
    off = 24
    for i in range(n_uv):
        decls.append(_Decl(5, i, 1, off))
        off += 8
    from types import SimpleNamespace

    return SimpleNamespace(
        declarators=decls,
        stride=off,
        bounds={8017: (0.0, 0.0, 0.0, 1.0), 8018: (0.0, 0.0, 0.0, 1.0)},
    )


_POS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
_UVQ = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_NRM = [(0.0, 0.0, 1.0)] * 4


def _bstate(positions=_POS, weights=None):
    return {
        "positions": list(positions),
        "weights": list(weights) if weights is not None else [{}] * len(positions),
    }


def test_fan_passes_tris_through():
    tris = [((0, 0), (1, 1), (2, 2))]
    assert triangulate_loops(tris) == tris


def test_fan_quad_winding():
    corners = [(0, 0), (1, 1), (2, 2), (3, 3)]
    assert triangulate_loops([corners]) == [
        (corners[0], corners[1], corners[2]),
        (corners[0], corners[2], corners[3]),
    ]


def test_fan_ngon_and_empty():
    corners = [(i, i) for i in range(5)]
    assert triangulate_loops([corners]) == [
        (corners[0], corners[1], corners[2]),
        (corners[0], corners[2], corners[3]),
        (corners[0], corners[3], corners[4]),
    ]
    assert triangulate_loops([]) == []


def test_rebuild_quad_fans_and_encodes():
    data = _Data(_POS, [(0, 1, 2, 3)], _UVQ, _NRM)
    vreg, ireg, nrows, ncounts, bounds, rmap = _rebuild_chunk(_chunk(), _Obj(data), _bstate())
    assert (nrows, ncounts) == (4, 6)
    assert len(vreg) == 4 * 32
    assert struct.unpack("<6H", ireg) == (0, 1, 2, 0, 2, 3)
    assert rmap == [0, 1, 2, 3]
    assert struct.unpack_from("<3f", vreg, 0) == (0.0, 0.0, 0.0)
    assert struct.unpack_from("<3f", vreg, 12) == (0.0, 0.0, 1.0)
    assert struct.unpack_from("<2f", vreg, 24) == (0.0, 1.0)  # V flipped
    assert struct.unpack_from("<2f", vreg, 32 + 24) == (1.0, 1.0)
    assert bounds[8017][:3] == (0.0, 0.0, 0.0)
    assert bounds[8018][:3] == (1.0, 1.0, 0.0)


def test_rebuild_shared_corners_stay_shared():
    data = _Data(
        _POS,
        [(0, 1, 2), (0, 2, 3)],
        [_UVQ[0], _UVQ[1], _UVQ[2], _UVQ[0], _UVQ[2], _UVQ[3]],
        _NRM + [_NRM[0], _NRM[2], _NRM[3]],
    )
    _, ireg, nrows, _, _, rmap = _rebuild_chunk(_chunk(), _Obj(data), _bstate())
    assert nrows == 4
    assert struct.unpack("<6H", ireg) == (0, 1, 2, 0, 2, 3)
    assert rmap == [0, 1, 2, 3]


def test_rebuild_seam_sides_split():
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.75, 0.75), (0.0, 1.0)]
    data = _Data(_POS, [(0, 1, 2), (0, 2, 3)], uvs, _NRM * 2)
    vreg, ireg, nrows, _, _, rmap = _rebuild_chunk(_chunk(), _Obj(data), _bstate())
    assert nrows == 6  # shared verts carry different loop UVs per face
    assert struct.unpack("<6H", ireg) == (0, 1, 2, 3, 4, 5)
    assert rmap == [0, 1, 2, 0, 2, 3]
    assert struct.unpack_from("<2f", vreg, 3 * 32 + 24) == (0.5, 0.5)


def test_rebuild_without_normals_zeroes():
    data = _Data(_POS, [(0, 1, 2, 3)], _UVQ)
    assert not hasattr(data, "corner_normals")
    vreg, _, nrows, _, _, _ = _rebuild_chunk(_chunk(), _Obj(data), _bstate())
    assert nrows == 4
    assert struct.unpack_from("<3f", vreg, 12) == (0.0, 0.0, 0.0)


def test_rebuild_missing_uv_layer_raises():
    data = _Data(_POS, [(0, 1, 2, 3)], _UVQ, _NRM)
    with pytest.raises(ValueError, match="UV layer 1 removed"):
        _rebuild_chunk(_chunk(n_uv=2), _Obj(data), _bstate())


def test_rebuild_unknown_dtype_raises():
    chunk = _chunk()
    chunk.declarators = [_Decl(0, 0, 9, 0)]
    data = _Data(_POS, [(0, 1, 2, 3)], _UVQ, _NRM)
    with pytest.raises(ValueError, match="no encoder for declarator type 9"):
        _rebuild_chunk(chunk, _Obj(data), _bstate())


def test_topology_changed_stays_value_error():
    assert issubclass(_TopologyChanged, ValueError)


# --- end to end: subdivide one tri of a real sample through _msh_bytes ---

from conftest import DATA_DIR
from thedas_io.export import _msh_bytes
from thedas_io.geometry import decode_chunk
from thedas_io.msh.description import MSHReader, load

_SAMPLE = DATA_DIR / "c_corspidr_3.msh"


class _Vert:
    def __init__(self, co, index):
        self.co = co
        self.index = index
        self.groups = []


class _MeshData:
    def __init__(self, positions, faces, loop_uvs, loop_normals):
        self.vertices = [_Vert(p, i) for i, p in enumerate(positions)]
        self.loops = [_Loop(v) for face in faces for v in face]
        self.polygons = []
        start = 0
        for face in faces:
            self.polygons.append(_Poly(tuple(face), start))
            start += len(face)
        self.uv_layers = [_Layer(loop_uvs)]
        self.corner_normals = [_Corner(v) for v in loop_normals]


class _MeshObj:
    def __init__(self, data, props=None):
        self.name = "E2E"
        self.data = data
        self.vertex_groups = []
        self._props = props or {}

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __setitem__(self, key, value):
        self._props[key] = value


def _subdivided(decoded):
    # 1:1 double of the decoded chunk with tri 0 split at its centroid.
    pos = list(decoded["positions"])
    a, b, c = decoded["triangles"][0]
    n = len(pos)
    pos.append(tuple(sum(x) / 3.0 for x in zip(pos[a], pos[b], pos[c])))
    faces = [tuple(t) for t in decoded["triangles"]]
    faces[0:1] = [(a, b, n), (b, c, n), (c, a, n)]
    uvs = decoded["uvs"][min(decoded["uvs"])]
    loop_uvs, loop_normals = [], []
    for tri in faces:
        for v in tri:
            if v == n:  # new corner: average of the subdivided tri
                loop_uvs.append(tuple(sum(c) / 3.0 for c in zip(uvs[a], uvs[b], uvs[c])))
                loop_normals.append(
                    decoded["normals"][a] if decoded["normals"] else (0.0, 0.0, 0.0)
                )
            else:  # 1:1 double: corner vert is its own source row
                loop_uvs.append(uvs[v])
                loop_normals.append(
                    decoded["normals"][v] if decoded["normals"] else (0.0, 0.0, 0.0)
                )
    return _MeshData(pos, faces, loop_uvs, loop_normals)


def test_end_to_end_subdivide_rebuilds_and_reparses():
    raw = _SAMPLE.read_bytes()
    mesh = load(_SAMPLE)
    decoded = decode_chunk(mesh, mesh.chunks[0])
    ncounts0 = len(decoded["triangles"]) * 3
    obj = _MeshObj(_subdivided(decoded))
    out = _msh_bytes({"model": mesh, "raw": raw, "objects": [(obj, mesh.chunks[0])]})
    assert out is not None
    back = MSHReader(out).mesh
    chunk = back.chunks[0]
    assert chunk.cells[8002] == ncounts0 + 6  # one tri became three
    assert chunk.cells[8001] == chunk.cells[8008]
    re = decode_chunk(back, chunk)
    assert re["vert_count"] == chunk.cells[8001]
    assert max(i for t in re["triangles"] for i in t) < re["vert_count"]
    assert len(obj.get("thedas_io_row_to_vert")) == re["vert_count"]
    assert len(back.vertex_data) > len(mesh.vertex_data)
    assert len(back.index_data) > len(mesh.index_data)


def test_rebuilt_output_is_stable():
    # A 1:1 double of rebuilt output re-verifies clean: second export is None.
    raw = _SAMPLE.read_bytes()
    mesh = load(_SAMPLE)
    decoded = decode_chunk(mesh, mesh.chunks[0])
    obj = _MeshObj(_subdivided(decoded))
    out = _msh_bytes({"model": mesh, "raw": raw, "objects": [(obj, mesh.chunks[0])]})
    back = MSHReader(out).mesh
    re = decode_chunk(back, back.chunks[0])
    uvs = re["uvs"][min(re["uvs"])]
    faces = [tuple(t) for t in re["triangles"]]
    data = _MeshData(
        list(re["positions"]),
        faces,
        [uvs[v] for t in faces for v in t],
        [re["normals"][v] if re["normals"] else (0.0, 0.0, 0.0) for t in faces for v in t],
    )
    obj2 = _MeshObj(data, {"thedas_io_row_to_vert": list(range(re["vert_count"]))})
    assert _msh_bytes({"model": back, "raw": out, "objects": [(obj2, back.chunks[0])]}) is None
