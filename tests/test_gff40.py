"""Tests for the GFF V4.0 reader.

Covers :class:`~gff40.description.GFFReader` against the real sample
files in ``data/``:

* ``c_corspidr_3.msh`` (MESH, 5 structs)
* ``c_corspidra_0.mmh`` (MMH, 35 structs)
* ``cai_lrgroofa_0.phy`` (PHY, 20 structs)

Checks: header fields, struct-def table layout (sequential field
offsets, data_offset consistency, 0xFF gap), struct lookup and
variable-data access, invalid magic handling.
"""

from __future__ import annotations

import struct

import pytest

from conftest import fourcc_bytes, load
from thedas_io.gff40.description import FLAGS, MAGIC, GFFReader

# (filename, file_type, nstruct, data_offset)
FILES = [
    ("c_corspidr_3.msh", b"MESH", 5, 544),
    ("c_corspidra_0.mmh", b"MMH ", 35, 3840),
    ("cai_lrgroofa_0.phy", b"PHY ", 20, 1944),
]

# Expected struct-def fourcc order per file (ascii, LE fourcc).
EXPECTED_TYPES = {
    "c_corspidr_3.msh": [b"mesh", b"decl", b"bnds", b"strm", b"chnk"],
    "c_corspidra_0.mmh": [b"mdlh", b"data", b"wtrl", b"bbox", b"bnds"],
    "cai_lrgroofa_0.phy": [b"mdlh", b"sphj", b"revj", b"plyj", b"prsj"],
}


@pytest.mark.parametrize("name,file_type,nstruct,data_offset", FILES)
def test_header(name, file_type, nstruct, data_offset):
    raw, reader = load(name)
    assert raw[0:8] == MAGIC
    assert reader.platform == b"PC  "
    assert reader.file_type == file_type
    assert reader.version == b"V0.1"
    assert reader.nstruct == nstruct
    assert reader.data_offset == data_offset
    assert len(reader.struct_defs) == nstruct


@pytest.mark.parametrize("name,file_type,nstruct,data_offset", FILES)
def test_struct_def_table(name, file_type, nstruct, data_offset):
    raw, reader = load(name)
    # Struct defs start right after the 28B header, 16B each.
    for i, entry in enumerate(reader.struct_defs):
        tid, fc, fo, sz = struct.unpack("<IIII", raw[28 + i * 16 : 28 + i * 16 + 16])
        assert entry.type_fourcc == tid
        assert entry.field_count == fc
        assert entry.field_offset == fo
        assert entry.struct_size == sz
        assert sz > 0  # phy zero-field structs use size 1, never 0

    # Field blocks are sequential with no holes.
    for prev, cur in zip(reader.struct_defs, reader.struct_defs[1:]):
        assert cur.field_offset == prev.field_offset + prev.field_count * 12

    # data_offset == end of last field block + 0xFF gap.
    last = reader.struct_defs[-1]
    fields_end = last.field_offset + last.field_count * 12
    assert fields_end <= data_offset
    gap = raw[fields_end:data_offset]
    assert set(gap) in ({0xFF}, set())  # phy has zero-length gap
    if name == "c_corspidr_3.msh":
        assert gap == b"\xff" * 4
    elif name == "c_corspidra_0.mmh":
        assert gap == b"\xff" * 12
    elif name == "cai_lrgroofa_0.phy":
        assert gap == b""


@pytest.mark.parametrize("name", [f[0] for f in FILES])
def test_first_struct_types(name):
    _, reader = load(name)
    got = [fourcc_bytes(e.type_fourcc) for e in reader.struct_defs[:5]]
    assert got == EXPECTED_TYPES[name]


def test_mmh_full_type_list():
    _, reader = load("c_corspidra_0.mmh")
    got = [fourcc_bytes(e.type_fourcc).decode("ascii") for e in reader.struct_defs]
    assert got[0] == "mdlh"
    assert got[-1] == "mshh"
    assert "node" in got and "nclt" in got


def test_get_struct_found():
    _, reader = load("c_corspidr_3.msh")
    mesh_id = int.from_bytes(b"mesh", "little")
    entry = reader.get_struct(mesh_id)
    assert entry.field_count == 6
    assert entry.struct_size == 24


def test_get_struct_missing():
    _, reader = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="not found"):
        reader.get_struct(0xDEADBEEF)


def _generic_ref_file(struct_index: int, address: int) -> bytes:
    """A two-instance GFF whose first struct holds one generic reference.

    The cell is not a pointer. It is two u32s stored inline in the instance:
    (flags << 16 | type) then the instance address, with the cell sitting at the
    field's data_index. Reading the first u32 as an offset instead is what made
    250 of 400 .phy files and every .ani in the game fail with
    "unpack_from requires a buffer of at least 1073743796 bytes".

    instance 0 (struct_size 12): [0:4] unused | [4:8] packed | [8:12] address
    instance 1 (struct_size 4): an empty struct for the generic to point at
    """
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 2, data_at)
    defs = struct.pack("<IIII", int.from_bytes(b"mdlh", "little"), 1, fields_at, 12)
    defs += struct.pack("<IIII", int.from_bytes(b"data", "little"), 0, fields_at + 12, 4)
    field = struct.pack("<IHHI", 6999, 0xFFFF, 0x2000, 4)  # label, type, flags, index
    body = b"\x00\x00\x00\x00"
    body += struct.pack("<II", (0x4000 << 16) | struct_index, address)
    body += b"\x00\x00\x00\x00"  # the empty instance at offset 12
    return header + defs + field + body


def test_generic_reference_is_an_inline_pair_not_a_pointer():
    raw = _generic_ref_file(struct_index=1, address=12)
    # the packed word sits in the cell, at the field's data_index
    cell = 28 + 2 * 16 + 12 + 4
    assert raw[cell : cell + 4] == struct.pack("<I", (0x4000 << 16) | 1)
    assert raw[cell + 4 : cell + 8] == struct.pack("<I", 12)

    reader = GFFReader(raw)
    root = reader.get_from_data_block(0, 0)
    generic = root[6999]
    # flags and type came out of the cell itself, and the address followed it
    assert generic["struct_index"] == 1
    assert generic["flags"] == 0x4000
    assert generic["offset"] == 12
    assert generic["value"] == {}


def test_generic_reference_rejects_a_nonsense_type():
    # a type that names no struct is a clean error, not a wild unpack. the old
    # pointer read turned the packed word 0x4000_0001 into offset 1073741825.
    reader = GFFReader(_generic_ref_file(struct_index=9999, address=12))
    with pytest.raises(ValueError, match="struct index 9999 out of range"):
        reader.get_from_data_block(0, 0)


def test_generic_reference_null_address_is_none():
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 2, data_at)
    defs = struct.pack("<IIII", int.from_bytes(b"mdlh", "little"), 1, fields_at, 12)
    defs += struct.pack("<IIII", int.from_bytes(b"data", "little"), 0, fields_at + 12, 4)
    field = struct.pack("<IHHI", 6999, 0xFFFF, 0x2000, 4)
    body = b"\x00\x00\x00\x00" + struct.pack("<II", (0x4000 << 16) | 1, 0xFFFFFFFF)
    root = GFFReader(header + defs + field + body).get_from_data_block(0, 0)
    assert root[6999]["value"] is None


def test_invalid_magic():
    raw, _ = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="Invalid GFF file magic"):
        GFFReader(b"BADMAGIC" + raw[8:])


def _struct_array_file(count: int, element_size: int) -> bytes:
    """A GFF whose root holds one struct-array field pointing at `count` rows."""
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 2, data_at)
    defs = struct.pack("<IIII", int.from_bytes(b"mdlh", "little"), 1, fields_at, 8)
    defs += struct.pack("<IIII", int.from_bytes(b"elem", "little"), 0, fields_at + 12, element_size)
    field = struct.pack("<IHHI", 6021, 1, FLAGS["STRUCT_ARRAY"], 0)  # ref cell at [0:4]
    body = struct.pack("<I", 4)  # the ref cell points at offset 4
    body += struct.pack("<I", count)  # the block's row count
    body += b"\x00" * (element_size * 3)  # three rows of room
    return header + defs + field + body


def test_struct_array_count_that_does_not_fit_is_a_named_error():
    # a truncated or foreign header reports a garbage count; range(n) would
    # try to build four billion dicts before instance() noticed
    with pytest.raises(ValueError, match="claims 4000000000 rows"):
        GFFReader(_struct_array_file(count=4_000_000_000, element_size=12)).get_from_data_block(
            0, 0
        )


def test_struct_array_of_zero_sized_elements_is_refused():
    # instance()'s bounds check is vacuous at size 0, so nothing would stop
    # the loop; the writer already rejects a zero-sized struct
    with pytest.raises(ValueError, match="element struct of size 0"):
        GFFReader(_struct_array_file(count=4_000_000_000, element_size=0)).get_from_data_block(0, 0)


def test_struct_array_still_reads_a_normal_one():
    rows = GFFReader(_struct_array_file(count=3, element_size=12)).get_from_data_block(0, 0)[6021]
    assert rows == [{}, {}, {}]


def test_a_file_shorter_than_the_header_is_a_named_error():
    # used to die as struct.error: unpack requires a buffer of 20 bytes
    for size in (8, 9, 20, 27):
        with pytest.raises(ValueError, match="header alone is"):
            GFFReader(MAGIC + b"\x00" * (size - 8))


def test_a_truncated_struct_table_is_a_named_error():
    # a header claiming more struct defs than the file holds
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 99, data_at)
    with pytest.raises(ValueError, match="struct def .* runs past the end"):
        GFFReader(header + b"\x00" * 40)


def test_a_struct_pointing_its_fields_past_the_file_is_a_named_error():
    # field_offset is a u32 straight out of the file; unvalidated it parses
    # whatever bytes are there as field descriptors
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 2, data_at)
    defs = struct.pack("<IIII", int.from_bytes(b"mdlh", "little"), 1, 999_999, 8)
    defs += struct.pack("<IIII", int.from_bytes(b"elem", "little"), 0, fields_at + 12, 4)
    field = struct.pack("<IHHI", 6021, 1, FLAGS["STRUCT_ARRAY"], 0)
    body = struct.pack("<I", 4) + struct.pack("<I", 0) + b"\x00" * 4
    with pytest.raises(ValueError, match="field 0 runs past the end"):
        GFFReader(header + defs + field + body)


def test_a_list_count_past_the_end_of_the_data_block_is_a_named_error():
    # the count word itself now goes through the bounds-checked read
    fields_at = 28 + 2 * 16
    data_at = fields_at + 12
    header = MAGIC + struct.pack("<4s4s4sII", b"PC  ", b"PHY ", b"V0.1", 2, data_at)
    defs = struct.pack("<IIII", int.from_bytes(b"mdlh", "little"), 1, fields_at, 4)
    defs += struct.pack("<IIII", int.from_bytes(b"elem", "little"), 0, fields_at + 12, 4)
    field = struct.pack("<IHHI", 6021, 4, FLAGS["SCALAR_LIST"], 0)
    body = struct.pack("<I", 900_000)  # ref points past the end of the data block
    with pytest.raises(ValueError, match="data block read out of range"):
        GFFReader(header + defs + field + body).get_from_data_block(0, 0)
