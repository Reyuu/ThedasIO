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
from thedas_io.gff40.description import MAGIC, GFFReader

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


def test_invalid_magic():
    raw, _ = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="Invalid GFF file magic"):
        GFFReader(b"BADMAGIC" + raw[8:])
