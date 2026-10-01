"""Tests for MSHWriter (Mesh -> bytes, fixed schema + GFFWriter.alloc*).

Sample: data/c_corspidr_3.msh — schema match, semantic roundtrip,
self-roundtrip, rename, and writer guardrails.
"""

from __future__ import annotations

import pytest

from conftest import DATA_DIR
from thedas_io.gff40.description import GFFReader, GFFWriter
from thedas_io.msh.description import _SCHEMA, MSHReader, MSHWriter, load

MSH_PATH = DATA_DIR / "c_corspidr_3.msh"


def decl_rows(chunk):
    return [
        (d.stream, d.offset, d.dtype, d.usage, d.usage_index, d.method) for d in chunk.declarators
    ]


def test_schema_matches_sample():
    raw = MSH_PATH.read_bytes()
    reader = GFFReader(raw)
    assert [(e.type_fourcc, e.struct_size) for e in reader.struct_defs] == [
        (fourcc, size) for fourcc, size, _ in _SCHEMA
    ]
    for sd, (_, _, fields) in zip(reader.struct_defs, _SCHEMA):
        assert [(f.label_id, f.type_id, f.flags, f.data_index) for f in sd.field_instances] == list(
            fields
        )
    total = sum(len(fields) for _, _, fields in _SCHEMA)
    fields_end = 28 + len(_SCHEMA) * 16 + total * 12
    assert raw[fields_end : reader.data_offset] == b"\xff" * (-fields_end % 16)


def test_write_reparses_equal():
    mesh = load(MSH_PATH)
    back = MSHReader(MSHWriter(mesh).write()).mesh
    assert back.name == mesh.name == "c_corspidr_3.msh"
    assert back.vertex_data == mesh.vertex_data
    assert back.index_data == mesh.index_data
    assert (back.index_format, back.instanced_stream) == (mesh.index_format, mesh.instanced_stream)
    assert len(back.chunks) == 1
    src, dst = mesh.chunks[0], back.chunks[0]
    assert dst.name == src.name and dst.cells == src.cells
    assert decl_rows(dst) == decl_rows(src)
    assert dst.bounds == src.bounds
    assert dst.additional_streams is None


def test_write_self_roundtrips():
    out = MSHWriter(load(MSH_PATH)).write()
    assert GFFWriter.from_bytes(out).write() == out


def test_rename_only_touches_name():
    writer = MSHWriter.from_bytes(MSH_PATH.read_bytes())
    mesh = load(MSH_PATH)
    writer.mesh.name = f"{mesh.name or ''}_renamed"
    back = MSHReader(writer.write()).mesh
    assert back.name == "c_corspidr_3.msh_renamed"
    assert back.vertex_data == mesh.vertex_data
    assert back.index_data == mesh.index_data
    assert decl_rows(back.chunks[0]) == decl_rows(mesh.chunks[0])


def test_missing_bounds_rejected():
    mesh = load(MSH_PATH)
    mesh.chunks[0].bounds = None
    with pytest.raises(ValueError, match="no bounds"):
        MSHWriter(mesh).write()


def test_streams_refused_without_sample():
    mesh = load(MSH_PATH)
    mesh.chunks[0].additional_streams = []
    with pytest.raises(NotImplementedError, match="8011"):
        MSHWriter(mesh).write()
