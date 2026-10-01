"""Tests for the MSH semantic layer (docs/PLAN.md P1, docs/MSH_GUIDE.md).

Sample: data/c_corspidr_3.msh — chunk cells, 8 declarator rows incl.
terminator, enum names, blob lengths, stride math, def-kind coverage.
"""

from __future__ import annotations

import pytest

from conftest import DATA_DIR
from thedas_io.msh.description import (
    KNOWN_FOURCCS,
    MSHReader,
    label_name,
    load,
)

MSH_PATH = DATA_DIR / "c_corspidr_3.msh"


def test_label_names_come_from_labels_table():
    assert label_name(8021) == "GFF_MESH_CHUNKS"
    assert label_name(8022) == "GFF_MESH_VERTEXDATA"
    assert label_name(8026) == "GFF_MESH_VERTEXDECLARATOR_STREAM"
    assert label_name(999999) == "label_999999"


def test_def_kinds_fully_mapped():
    reader = MSHReader(MSH_PATH.read_bytes())
    kinds = {e.type_fourcc.to_bytes(4, "little").decode("ascii") for e in reader.gff.struct_defs}
    assert kinds == {"mesh", "decl", "bnds", "strm", "chnk"}
    assert {int.from_bytes(k.encode("ascii"), "little") for k in kinds} <= KNOWN_FOURCCS


def test_mesh_name_and_chunk_name():
    mesh = load(MSH_PATH)
    assert mesh.name == "c_corspidr_3.msh"
    assert len(mesh.chunks) == 1
    assert mesh.chunks[0].name == "C_CORSPIDR_Mesh1"


def test_chunk_cells_retained():
    chunk = load(MSH_PATH).chunks[0]
    assert chunk.cells == {
        8000: 52,
        8001: 1100,
        8002: 1968,
        8003: 0,
        8004: 0,
        8005: 0,
        8006: 0,
        8007: 0,
        8008: 1100,
        8009: 0,
        8034: 1,
    }


def test_declarator_rows_and_names():
    chunk = load(MSH_PATH).chunks[0]
    assert len(chunk.declarators) == 8
    rows = [(d.stream, d.offset, d.type_name, d.usage_name) for d in chunk.declarators[:7]]
    assert rows == [
        (0, 0, "FLOAT16_4", "POSITION"),
        (0, 8, "FLOAT16_2", "TEXCOORD"),
        (0, 12, "FLOAT16_4", "TANGENT"),
        (0, 20, "FLOAT16_4", "BINORMAL"),
        (0, 28, "FLOAT16_4", "NORMAL"),
        (0, 36, "FLOAT16_4", "BLENDWEIGHT"),
        (0, 44, "SHORT4", "BLENDINDICES"),
    ]
    last = chunk.declarators[7]
    assert last.is_terminator and last.stream == -1
    assert not any(d.is_terminator for d in chunk.declarators[:7])


def test_stride_matches_vertexsize_cell():
    chunk = load(MSH_PATH).chunks[0]
    assert chunk.stride == 52 == chunk.cells[8000]


def test_blobs_and_bounds():
    mesh = load(MSH_PATH)
    assert len(mesh.vertex_data) == 57216
    assert len(mesh.index_data) == 3952
    assert mesh.index_format == 0
    assert mesh.instanced_stream == 0
    assert set(mesh.chunks[0].bounds or {}) == {8017, 8018, 8019}
    assert mesh.chunks[0].additional_streams is None


def test_index_count_cells_reconciled():
    # 8002 = indices_count (faces x 3); 8007/8008 = min index / verts referenced.
    for path, verts, indices, faces in (
        ("c_corspidr_3.msh", 1100, 1968, 656),
        ("c_corspidr_0.msh", 5448, 17136, 5712),
    ):
        chunk = load(DATA_DIR / path).chunks[0]
        assert chunk.cells[8001] == verts
        assert chunk.cells[8002] == indices == faces * 3
        assert chunk.cells[8007] == 0  # MININDEX
        assert chunk.cells[8008] == verts  # VERTICESREFERENCED
        assert indices <= 0xFFFF  # u16 index width on samples


def test_get_chunk_out_of_range():
    mesh = load(MSH_PATH)
    with pytest.raises(ValueError, match="out of range"):
        mesh.get_chunk(1)


def test_wrong_file_type_rejected():
    with pytest.raises(ValueError, match="Invalid MSH file_type"):
        MSHReader((DATA_DIR / "c_corspidra_0.mmh").read_bytes())
