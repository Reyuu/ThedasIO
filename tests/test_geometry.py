from __future__ import annotations

import pytest

from conftest import DATA_DIR
from thedas_io.geometry import decode_chunk, weld_positions
from thedas_io.msh.description import load

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
