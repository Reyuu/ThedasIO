from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.geometry import decode_chunk, index_width, weld_positions
from thedas_io.msh.description import Mesh, load

MSH_PATH = DATA_DIR / "c_corspidr_3.msh"


def test_decode_counts():
    mesh = load(MSH_PATH)
    chunk = mesh.chunks[0]
    build = decode_chunk(mesh, chunk)
    assert build["vert_count"] == 1100
    assert len(build["positions"]) == 1100
    assert len(build["triangles"]) == 656
    assert len(build["normals"]) == 1100
    assert len(build["uvs"][0]) == 1100


def test_positions_sane():
    mesh = load(MSH_PATH)
    build = decode_chunk(mesh, mesh.chunks[0])
    xs = [p[0] for p in build["positions"]]
    assert min(xs) > -50 and max(xs) < 50
    assert all(len(p) == 3 for p in build["positions"])


def test_groups_reference_bones():
    mesh = load(MSH_PATH)
    build = decode_chunk(mesh, mesh.chunks[0])
    assert build["groups"]
    assert all(0 <= b < 66 for b in build["groups"])
    total = sum(w for members in build["groups"].values() for _, w in members)
    assert total > 0


def test_rejects_non_trilist():
    mesh = load(MSH_PATH)
    mesh.chunks[0].cells[8003] = 1
    with pytest.raises(ValueError, match="trilist"):
        decode_chunk(mesh, mesh.chunks[0])


def _one_chunk(verts, index_bytes, index_count, stride=12, decl_type=2, decl_usage=0):
    # a chunk with one FLOAT3 declarator and one hand-fed index blob. index_count
    # is 8002 (elements, not bytes) and is passed in rather than derived, so a
    # test can make it disagree with the blob on purpose
    chunk = {
        2: "c",
        8000: stride,
        8001: verts,
        8002: index_count,
        8003: 0,
        8006: 0,
        8008: verts,
        8009: 0,
        8025: [{8026: 0, 8027: 0, 8028: decl_type, 8029: decl_usage, 8030: 0, 8031: 0}],
    }
    return Mesh({2: "m", 8021: [chunk], 8022: [0] * (stride * verts), 8023: list(index_bytes)})


def test_index_width_follows_the_vertex_count_not_the_index_count():
    # c_giantbr_0.msh shape: 96795 indices over 22105 vertices, stored as u16.
    # Keying the width off 8002 reads half a u16 blob as u32 and dies.
    tris = 21846  # 65538 indices, just past the u16 ceiling
    assert tris * 3 > 0xFFFF
    mesh = _one_chunk(
        verts=4, index_bytes=struct.pack(f"<{tris * 3}H", *([0, 1, 2] * tris)), index_count=tris * 3
    )

    build = decode_chunk(mesh, mesh.chunks[0])

    assert build["vert_count"] == 4
    assert len(build["triangles"]) == tris


def test_index_width_goes_wide_past_65535_vertices():
    mesh = _one_chunk(verts=65536, index_bytes=struct.pack("<3I", 0, 65535, 1), index_count=3)

    build = decode_chunk(mesh, mesh.chunks[0])

    assert build["vert_count"] == 65536
    assert build["triangles"] == [(0, 65535, 1)]


def test_index_width_switches_exactly_at_the_boundary():
    assert index_width(0xFFFF) == 2
    assert index_width(0x10000) == 4


def test_index_blob_shorter_than_the_chunk_claims():
    # a clear message beats struct.error's "requires a buffer of N bytes"
    mesh = _one_chunk(verts=4, index_bytes=struct.pack("<3H", 0, 1, 2), index_count=6)

    with pytest.raises(ValueError, match="index blob holds 6B, 4 verts need 12B"):
        decode_chunk(mesh, mesh.chunks[0])


def test_weld_positions_collapses_exact_duplicates():
    unique, row_to_vert = weld_positions([(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 0, 0)])
    assert unique == [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    assert row_to_vert == [0, 1, 0, 2, 1]


def test_weld_positions_identity_and_empty():
    assert weld_positions([(0, 0, 0), (1, 1, 1)]) == ([(0, 0, 0), (1, 1, 1)], [0, 1])
    assert weld_positions([]) == ([], [])


def test_weld_sample_matches_measured():
    # c_corspidr_3.msh: 1100 rows -> 330 verts; every row recovers.
    mesh = load(MSH_PATH)
    build = decode_chunk(mesh, mesh.chunks[0])
    unique, row_to_vert = weld_positions(build["positions"])
    assert len(unique) == 330
    assert len(row_to_vert) == 1100
    assert all(build["positions"][r] == unique[row_to_vert[r]] for r in range(1100))
    remapped = [tuple(row_to_vert[v] for v in tri) for tri in build["triangles"]]
    assert all(v < 330 for tri in remapped for v in tri)
    assert len(remapped) == 656
