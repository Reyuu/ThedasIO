"""Tests for MSHWriter (Mesh -> bytes, fixed schema + GFFWriter.alloc*).

Sample: data/c_corspidr_3.msh — schema match, semantic roundtrip,
self-roundtrip, rename, and writer guardrails.
"""

from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.gff40.description import FLAGS, GFFField, GFFReader, GFFWriter
from thedas_io.msh.description import _SCHEMA, MSHReader, MSHWriter, _chunk_bases, load

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


def test_declarator_rows_match_the_file_row_size():
    # DA2's decl struct drops the 8031 method cell (20B). Writing 24B rows into
    # it shifts every row after the first, so the exported chunk reads back with
    # garbage streams/usages.
    mesh = load(MSH_PATH)
    chunk = mesh.chunks[0]
    rows = len(chunk.declarators)

    assert mesh.decl_size == 24  # DAO fixture keeps the method cell
    assert len(MSHWriter._declarator_bytes(chunk, 24)) == 4 + rows * 24
    assert len(MSHWriter._declarator_bytes(chunk, 20)) == 4 + rows * 20

    with pytest.raises(ValueError, match="not modelled"):
        MSHWriter._declarator_bytes(chunk, 28)


def test_reader_reports_the_decl_row_size_it_parsed():
    # the writer uses this to lay rows out like the file it came from
    assert MSHReader(MSH_PATH.read_bytes()).mesh.decl_size == 24


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


def _splice_unknown_def(raw: bytes, fourcc: bytes) -> bytes:
    """Add one struct def with a fourcc the schema does not know."""
    import struct as _struct

    r = GFFReader(raw)
    # 16 bytes of defs are inserted before the field block, so every absolute
    # offset into the file from there on shifts up by 16
    header = raw[:8] + _struct.pack(
        "<4s4s4sII", r.platform, r.file_type, r.version, r.nstruct + 1, r.data_offset + 16
    )
    defs = bytearray(raw[28 : 28 + r.nstruct * 16])
    # every field_offset shifts by the 16 bytes just inserted
    for i in range(r.nstruct):
        at = 8 + i * 16
        fo = _struct.unpack_from("<I", defs, at)[0]
        _struct.pack_into("<I", defs, at, fo + 16)
    fields = raw[28 + r.nstruct * 16 : r.data_offset]
    extra = _struct.pack("<IIII", int.from_bytes(fourcc, "little"), 0, 0, 4)
    return header + bytes(defs) + extra + fields + raw[r.data_offset :]


def test_an_unknown_struct_kind_loads_and_survives_export():
    # one new fourcc used to make the whole file unreadable; the reader walks
    # the def table generically and the writer clones it, so it has to survive
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    spliced = _splice_unknown_def(raw, b"zzzz")

    reader = MSHReader(spliced)
    assert reader.unknown_fourccs == ["zzzz"]
    assert reader.mesh.chunks  # the file still reads as a mesh

    out = MSHWriter.from_reader(reader).write()
    assert "zzzz" in {
        sd.type_fourcc.to_bytes(4, "little").decode("ascii", "replace")
        for sd in GFFReader(out).struct_defs
    }


def test_an_untouched_mesh_keeps_its_size_and_def_table():
    # clone-then-patch must not re-lay out a file that did not change
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    reader = MSHReader(raw)
    out = MSHWriter.from_reader(reader).write()
    assert len(out) == len(raw)
    assert [sd.type_fourcc for sd in GFFReader(out).struct_defs] == [
        sd.type_fourcc for sd in reader.gff.struct_defs
    ]


def test_an_edited_mesh_still_roundtrips_through_the_patch_path():
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    writer = MSHWriter.from_bytes(raw)
    writer.mesh.name = "renamed"
    writer.mesh.vertex_data = list(writer.mesh.vertex_data)[:-8]
    back = MSHReader(writer.write()).mesh
    assert back.name == "renamed"
    assert back.vertex_data == writer.mesh.vertex_data
    assert back.chunks


def test_an_edited_chunk_region_is_written_back():
    # 8021 is a LIST_OF_REFS, not a struct array: count then u32 offsets, with
    # the chnk instance at offset 32. Reading that list as inline instances put
    # the chunk base ~318KB off and the edited cells were patched into the
    # wrong place entirely.
    raw = (DATA_DIR / "c_corspidr_0.msh").read_bytes()
    reader = MSHReader(raw)
    base = reader.mesh
    chunk = base.chunks[0]
    writer = MSHWriter(base, reader)

    vblob = bytearray(base.vertex_data)
    vblob += b"\xaa" * chunk.stride
    base.vertex_data = list(vblob)
    chunk.cells[8001] = chunk.cells[8001] + 1
    chunk.cells[8006] = len(vblob)

    back = MSHReader(writer.write()).mesh
    assert back.chunks[0].cells[8006] == len(vblob)
    assert back.chunks[0].cells[8001] == chunk.cells[8001]
    assert back.vertex_data[-1] == 0xAA


def test_the_chunk_list_shape_is_read_from_the_flags():
    from thedas_io.msh.description import _chunk_list

    for name in ("c_corspidr_0.msh", "c_corspidr_3.msh", "qm_uhm_basb_0.msh"):
        gff = MSHReader((DATA_DIR / name).read_bytes()).gff
        field, _ref, _size, count = _chunk_list(gff)
        assert field.flags == FLAGS["LIST_OF_REFS"], name
        assert count == len(MSHReader((DATA_DIR / name).read_bytes()).mesh.chunks)


def test_a_chunk_cell_outside_the_schema_survives_export():
    # the chnk struct's tail is unused; a cell there is neither in
    # L_CHUNK_CELLS nor written by _fill_chunk, so it only survives because the
    # patch path rewrites the file's own instance in place
    raw = bytearray((DATA_DIR / "c_corspidr_0.msh").read_bytes())
    probe = MSHReader(bytes(raw))
    base = _chunk_bases(probe.gff)[0]
    at = probe.gff.data_offset + base + 104
    struct.pack_into("<I", raw, at, 0xABCDEF01)

    reader = MSHReader(bytes(raw))
    chnk = next(
        sd for sd in reader.gff.struct_defs if sd.type_fourcc == int.from_bytes(b"chnk", "little")
    )
    chnk.field_instances.append(GFFField(6999, 4, 0, 104))

    out = MSHReader(MSHWriter.from_reader(reader).write())
    back_base = _chunk_bases(out.gff)[0]
    got = struct.unpack_from("<I", out.gff.data, out.gff.data_offset + back_base + 104)[0]
    assert got == 0xABCDEF01
    # known as bytes in the file, but the Chunk model does not surface it
    assert 6999 not in out.mesh.chunks[0].cells


def test_a_dword_terminator_is_still_a_terminator():
    # 8026 is typed INT here and DWORD in other files. The D3DDECL_END row is
    # all ones either way, and missing one folds a dead row into Chunk.stride.
    raw = (DATA_DIR / "c_corspidr_0.msh").read_bytes()
    chunk = MSHReader(raw).mesh.chunks[0]
    last = chunk.declarators[-1]
    assert last.stream == -1 and last.is_terminator

    signed_stride = chunk.stride
    last.stream = 0xFFFFFFFF
    assert last.is_terminator
    assert chunk.stride == signed_stride


def test_a_dword_terminator_writes_back_as_the_signed_spelling():
    # the declarator blob is packed as a signed int, so an all-ones DWORD has
    # to be normalised on the way out. The writer is built first, as the export
    # path does, so the change is seen as an edit and the chunk re-encoded.
    reader = MSHReader((DATA_DIR / "c_corspidr_0.msh").read_bytes())
    writer = MSHWriter.from_reader(reader)
    writer.mesh.chunks[0].declarators[-1].stream = 0xFFFFFFFF

    out = MSHReader(writer.write())

    last = out.mesh.chunks[0].declarators[-1]
    assert last.stream == -1 and last.is_terminator
