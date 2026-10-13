from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.geometry import (
    INDEX_FORMAT_U32,
    chunk_index_format,
    chunk_index_width,
    decode_chunk,
    index_width,
    weld_positions,
)
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


def _one_chunk(
    verts,
    index_bytes,
    index_count,
    stride=12,
    decl_type=2,
    decl_usage=0,
    chunk_format=None,
    mesh_format=None,
):
    # a chunk with one FLOAT3 declarator and one hand-fed index blob. index_count
    # is 8002 (elements, not bytes) and is passed in rather than derived, so a
    # test can make it disagree with the blob on purpose. chunk_format is 8004
    # and mesh_format is 8032; both omitted means the field is absent
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
    if chunk_format is not None:
        chunk[8004] = chunk_format
    root = {2: "m", 8021: [chunk], 8022: [0] * (stride * verts), 8023: list(index_bytes)}
    if mesh_format is not None:
        root[8032] = mesh_format
    return Mesh(root)


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


def test_declared_format_beats_the_vertex_count():
    # the file serializes its own width; the vertex count is only a fallback.
    # u32 declared over 4 verts: the blob is genuinely 4 bytes per index and a
    # vertex-count rule would read it as u16 and produce nonsense triangles.
    mesh = _one_chunk(
        verts=4, index_bytes=struct.pack("<3I", 0, 2, 3), index_count=3, mesh_format=1
    )

    assert decode_chunk(mesh, mesh.chunks[0])["triangles"] == [(0, 2, 3)]


def test_declared_format_beats_the_vertex_count_the_other_way():
    # zz_wrf_rc_0.msh shape in miniature: >65535 verts declared u16. The
    # heuristic would pick u32 and blow up on a blob that is u16.
    tris = 21846
    mesh = _one_chunk(
        verts=4,
        index_bytes=struct.pack(f"<{tris * 3}H", *([0, 1, 2] * tris)),
        index_count=tris * 3,
        mesh_format=0,
    )

    assert len(decode_chunk(mesh, mesh.chunks[0])["triangles"]) == tris


def test_chunk_format_wins_over_mesh_format():
    # 8004 is per chunk and mirrors 8032; prefer the nearer declaration
    mesh = _one_chunk(
        verts=4,
        index_bytes=struct.pack("<3I", 0, 1, 2),
        index_count=3,
        chunk_format=1,
        mesh_format=0,
    )

    assert decode_chunk(mesh, mesh.chunks[0])["triangles"] == [(0, 1, 2)]


def test_absent_format_falls_back_to_the_vertex_count():
    # hand-built fixtures carry neither 8004 nor 8032, so the rule still needs
    # to work without them
    mesh = _one_chunk(verts=4, index_bytes=struct.pack("<3H", 0, 1, 2), index_count=3)

    assert decode_chunk(mesh, mesh.chunks[0])["triangles"] == [(0, 1, 2)]
    assert chunk_index_width(mesh.chunks[0]) == 2


def test_unrecognized_format_value_falls_back_to_the_vertex_count():
    # index_format comes off disk unvalidated. A value we do not understand must
    # degrade to the heuristic, never crash and never silently pick a width.
    mesh = _one_chunk(
        verts=4, index_bytes=struct.pack("<3H", 0, 1, 2), index_count=3, mesh_format=0xA5A5
    )

    assert decode_chunk(mesh, mesh.chunks[0])["triangles"] == [(0, 1, 2)]


def test_chunk_index_format_round_trips_the_width_it_was_asked_about():
    # export writes this back; it must never contradict the bytes next to it
    mesh = _one_chunk(verts=4, index_bytes=struct.pack("<3H", 0, 1, 2), index_count=3)
    chunk = mesh.chunks[0]

    assert chunk_index_format(chunk, width=2) == 0
    assert chunk_index_format(chunk, width=4) == INDEX_FORMAT_U32


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


def test_unmodelled_vertex_channels_are_reported_not_zeroed():
    # a rebuilt row starts as zeros and only modelled usages get written over
    # them, so PSIZE / colour / fog / depth used to become 0 without a word
    from types import SimpleNamespace

    from thedas_io.export import _unmodelled_usages
    from thedas_io.msh.description import MSHReader

    chunk = MSHReader((DATA_DIR / "c_corspidr_0.msh").read_bytes()).mesh.chunks[0]
    assert _unmodelled_usages(chunk) == []  # the shipped mesh only uses ones we model

    chunk.declarators.append(SimpleNamespace(usage=4, is_terminator=False))
    chunk.declarators.append(SimpleNamespace(usage=10, is_terminator=False))
    assert _unmodelled_usages(chunk) == [4, 10]

    # a terminator is not a channel and must not be reported
    chunk.declarators.append(SimpleNamespace(usage=99, is_terminator=True))
    assert _unmodelled_usages(chunk) == [4, 10]
