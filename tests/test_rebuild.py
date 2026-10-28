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
        cells={},  # no 8001/8004: the width rule falls back to the row count
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
    assert tuple(ireg) == (0, 1, 2, 0, 2, 3)
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
    assert tuple(ireg) == (0, 1, 2, 0, 2, 3)
    assert rmap == [0, 1, 2, 3]


def test_rebuild_seam_sides_split():
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.75, 0.75), (0.0, 1.0)]
    data = _Data(_POS, [(0, 1, 2), (0, 2, 3)], uvs, _NRM * 2)
    vreg, ireg, nrows, _, _, rmap = _rebuild_chunk(_chunk(), _Obj(data), _bstate())
    assert nrows == 6  # shared verts carry different loop UVs per face
    assert tuple(ireg) == (0, 1, 2, 3, 4, 5)
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


def test_pack_quantises_unit_channels_by_usage_not_dtype():
    from thedas_io.export import _pack
    from thedas_io.geometry import _norm8_rows

    # UBYTE4N normal: -1..1 with the 128 bias, and the decoder reads it back
    blob = _pack("<4B", [-1.0, 0.0, 1.0, 1.0], 3, 8)
    assert struct.unpack("<4B", blob) == (1, 128, 255, 255)
    assert _norm8_rows([struct.unpack("<4B", blob)])[0] == (-1.0, 0.0, 1.0)

    # dtype 7 is a raw BLENDINDICES cell as well as a normalised normal, so the
    # usage has to decide: scaling an index by 32767 put every vertex on one bone
    blob = _pack("<4h", [0, 1, 63, 0], 2, 7)
    assert struct.unpack("<4h", blob) == (0, 1, 63, 0)

    # weights stay 0..1 into 0..255
    blob = _pack("<4B", [0.0, 0.5, 1.0, 0.0], 1, 8)
    assert struct.unpack("<4B", blob) == (0, 128, 255, 0)


# --- end to end: subdivide one tri of a real sample through _msh_bytes ---

from conftest import DATA_DIR
from thedas_io.export import _msh_bytes
from thedas_io.geometry import (
    INDEX_FORMAT_U32,
    chunk_index_format,
    chunk_index_width,
    decode_chunk,
)
from thedas_io.msh.description import Chunk, MSHReader, MSHWriter, load

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


def test_rebuild_writes_an_index_format_that_matches_the_bytes():
    # 8004/8032 are round-tripped verbatim by the writer, so a rebuild that
    # changes the width must update them or the file contradicts itself.
    raw = _SAMPLE.read_bytes()
    mesh = load(_SAMPLE)
    decoded = decode_chunk(mesh, mesh.chunks[0])
    obj = _MeshObj(_subdivided(decoded))
    out = _msh_bytes({"model": mesh, "raw": raw, "objects": [(obj, mesh.chunks[0])]})
    back = MSHReader(out).mesh
    chunk = back.chunks[0]

    width = chunk_index_width(chunk, back)
    assert chunk.cells[8004] == chunk_index_format(chunk, back)
    assert back.index_format == (1 if width == 4 else 0)
    # the declared width is what actually fits the region we wrote
    ncounts = chunk.cells[8002]
    assert len(back.index_data) >= ncounts * width
    # and decoding by the declared width reproduces the triangles
    assert len(decode_chunk(back, chunk)["triangles"]) == ncounts // 3


def test_rebuild_returns_indices_packable_at_either_width():
    # the caller decides the width now, so _rebuild_chunk hands back a flat
    # list rather than bytes packed at one width
    _vreg, ireg, _nrows, _ncounts, _bounds, _rmap = _rebuild_chunk(
        _chunk(), _Obj(_Data(_POS, [(0, 1, 2, 3)], _UVQ, _NRM)), _bstate()
    )

    assert tuple(ireg) == (0, 1, 2, 0, 2, 3)
    assert struct.unpack("<6H", struct.pack("<6H", *ireg)) == (0, 1, 2, 0, 2, 3)
    assert struct.unpack("<6I", struct.pack("<6I", *ireg)) == (0, 1, 2, 0, 2, 3)


def _big_obj(n):
    # a mesh with enough rows to blow past u16, as a subdivide of a small
    # sample would: one row per face corner, so no two corners share a vertex
    faces = [(i, i + 1, i + 2) for i in range(n - 2)]
    uv = [(0.0, 0.0)] * len(faces) * 3
    nrm = [(0.0, 0.0, 1.0)] * len(faces) * 3
    return _MeshObj(_MeshData([(0.0, 0.0, 0.0)] * n, faces, uv, nrm))


def test_export_widens_the_whole_mesh_when_a_rebuild_outgrows_u16():
    # c_corspidr_3.msh ships u16. Push one chunk's rebuild past 65535 rows and
    # the whole mesh is promoted to u32: 8009 is a mesh-wide element offset, so
    # every chunk's region is re-packed and re-offset together.
    mesh = load(_SAMPLE)
    assert chunk_index_width(mesh.chunks[0], mesh) == 2
    obj = _big_obj(70000)

    out = _msh_bytes(
        {"model": mesh, "raw": _SAMPLE.read_bytes(), "objects": [(obj, mesh.chunks[0])]}
    )
    back = MSHReader(out).mesh
    chunk = back.chunks[0]

    assert back.index_format == INDEX_FORMAT_U32
    assert chunk.cells[8004] == INDEX_FORMAT_U32
    assert chunk.cells[8001] > 0xFFFF
    # the region is u32 and decodes cleanly through the declared format
    assert chunk_index_width(chunk, back) == 4
    re = decode_chunk(back, chunk)
    assert re["vert_count"] == chunk.cells[8001]
    assert len(re["triangles"]) == chunk.cells[8002] // 3
    assert max(i for t in re["triangles"] for i in t) < re["vert_count"]


def _two_chunk_sample(tmp_path):
    # c_corspidr_3.msh with a second chunk appended, written through the real
    # writer so the fixture is a genuine two-chunk file rather than a patched
    # in-memory object (export re-reads item["raw"], so an in-memory twin would
    # simply be discarded).
    #
    # The twin shares chunk 0's vertex region and takes its own 6 indices out
    # of chunk 0's blob (element offset 6), so both chunks stay independently
    # decodable without either being truncated out from under a real index.
    mesh = load(_SAMPLE)
    mesh.chunks[0].cells[8002] = 6
    twin = Chunk(
        {
            2: "Twin",
            **{k: v for k, v in mesh.chunks[0].cells.items() if k != 8009},
            8002: 6,
            8006: 0,
            8009: 6,
            8020: mesh.chunks[0].bounds,
            8025: [
                {
                    8026: d.stream,
                    8027: d.offset,
                    8028: d.dtype,
                    8029: d.usage,
                    8030: d.usage_index,
                    8031: d.method,
                }
                for d in mesh.chunks[0].declarators
            ],
        }
    )
    mesh.chunks.append(twin)
    path = tmp_path / "two.msh"
    path.write_bytes(MSHWriter(mesh).write())
    return MSHReader(path.read_bytes()).mesh, path.read_bytes()


def test_widening_repacks_the_untouched_sibling_chunks_too(tmp_path):
    # the whole point: a sibling chunk left at u16 would be read from a start
    # offset computed for the wrong width, i.e. garbage indices
    mesh, raw = _two_chunk_sample(tmp_path)
    assert len(mesh.chunks) == 2
    assert chunk_index_width(mesh.chunks[1], mesh) == 2
    obj = _big_obj(70000)

    out = _msh_bytes({"model": mesh, "raw": raw, "objects": [(obj, mesh.chunks[0])]})
    back = MSHReader(out).mesh

    assert len(back.chunks) == 2
    for chunk in back.chunks:
        assert chunk.cells[8004] == INDEX_FORMAT_U32, "sibling left at the old width"
        assert chunk_index_width(chunk, back) == 4
        # each chunk reads back as its own triangles at the new width
        re = decode_chunk(back, chunk)
        assert max(i for t in re["triangles"] for i in t) < re["vert_count"]
    # the untouched twin kept its own indices through the repack, and its
    # region is at a *different* u32 offset than the rebuilt chunk's
    assert back.chunks[1].cells[8002] == 6
    assert back.chunks[0].cells[8009] != back.chunks[1].cells[8009]


def test_widening_preserves_an_edited_sibling_chunk(tmp_path):
    # Two chunks in one file, both rebuilt, one of them past 65535 rows. The
    # repack walks every chunk, so the small one must survive the round trip at
    # the new width with its own geometry intact -- not be clobbered by the
    # sibling's blob.
    mesh, raw = _two_chunk_sample(tmp_path)
    base = decode_chunk(MSHReader(raw).mesh, mesh.chunks[0])
    faces = [tuple(t) for t in base["triangles"]]
    used = sorted({v for t in faces for v in t})
    remap = {v: i for i, v in enumerate(used)}
    small = _MeshObj(
        _MeshData(
            [tuple(base["positions"][v]) for v in used],
            [tuple(remap[v] for v in t) for t in faces],
            [base["uvs"][0][v] for t in faces for v in t],
            [base["normals"][v] for t in faces for v in t],
        )
    )

    out = _msh_bytes(
        {
            "model": mesh,
            "raw": raw,
            "objects": [(small, mesh.chunks[0]), (_big_obj(70000), mesh.chunks[1])],
        }
    )
    back = MSHReader(out).mesh

    assert back.index_format == INDEX_FORMAT_U32
    for chunk in back.chunks:
        assert chunk.cells[8004] == INDEX_FORMAT_U32
    # the small chunk kept its own triangles, decoded at u32
    assert back.chunks[0].cells[8002] == len(faces) * 3
    re0 = decode_chunk(back, back.chunks[0])
    assert max(i for t in re0["triangles"] for i in t) < re0["vert_count"]
    assert len(re0["triangles"]) == len(faces)
    # and the two chunks occupy distinct regions
    assert back.chunks[0].cells[8009] != back.chunks[1].cells[8009]


def test_export_below_the_ceiling_still_writes_u16():
    # an ordinary-sized rebuild must not trigger the repack
    mesh = load(_SAMPLE)
    decoded = decode_chunk(mesh, mesh.chunks[0])
    obj = _MeshObj(_subdivided(decoded))
    out = _msh_bytes(
        {"model": mesh, "raw": _SAMPLE.read_bytes(), "objects": [(obj, mesh.chunks[0])]}
    )
    back = MSHReader(out).mesh

    assert chunk_index_width(back.chunks[0], back) == 2
    assert back.index_format == 0


def test_widened_mesh_stays_widened_on_a_second_export():
    # once promoted the file declares u32, so a later rebuild must keep it there
    mesh = load(_SAMPLE)
    obj = _big_obj(70000)
    first = MSHReader(
        _msh_bytes({"model": mesh, "raw": _SAMPLE.read_bytes(), "objects": [(obj, mesh.chunks[0])]})
    ).mesh
    obj2 = _MeshObj(_subdivided(decode_chunk(first, first.chunks[0])))
    second = _msh_bytes(
        {
            "model": first,
            "raw": _msh_bytes(
                {"model": mesh, "raw": _SAMPLE.read_bytes(), "objects": [(obj, mesh.chunks[0])]}
            ),
            "objects": [(obj2, first.chunks[0])],
        }
    )
    back = MSHReader(second).mesh

    assert back.index_format == INDEX_FORMAT_U32
    assert chunk_index_width(back.chunks[0], back) == 4


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
