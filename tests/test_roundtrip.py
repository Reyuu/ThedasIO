"""Roundtrip: read -> write byte equality (docs/PLAN.md P3).

Covers all three samples (msh gap 4, mmh gap 12, phy gap empty):
duplicate labels, zero-field structs, generic lists, NULL_REFs.
"""

from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.gff40.description import GFFReader, GFFWriter

FILES = ["c_corspidr_3.msh", "c_corspidra_0.mmh", "cai_lrgroofa_0.phy"]


@pytest.mark.parametrize("name", FILES)
def test_roundtrip_byte_exact(name):
    raw = (DATA_DIR / name).read_bytes()
    assert GFFWriter.from_bytes(raw).write() == raw


@pytest.mark.parametrize("name", FILES)
def test_roundtrip_recomputes_header(name):
    raw = (DATA_DIR / name).read_bytes()
    reader = GFFReader(raw)
    out = GFFWriter.from_reader(reader).write()
    nstruct, data_offset = struct.unpack("<II", out[20:28])
    assert nstruct == reader.nstruct
    assert data_offset == reader.data_offset
    # Def table recomputed in original order with sequential field offsets.
    for i, entry in enumerate(reader.struct_defs):
        tid, fc, _fo, sz = struct.unpack("<IIII", out[28 + i * 16 : 28 + i * 16 + 16])
        assert (tid, fc, sz) == (entry.type_fourcc, entry.field_count, entry.struct_size)
    last = reader.struct_defs[-1]
    fields_end = last.field_offset + last.field_count * 12
    assert out[fields_end:data_offset] == raw[fields_end : reader.data_offset]


def test_from_reader_matches_from_bytes():
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    assert GFFWriter.from_reader(GFFReader(raw)).write() == GFFWriter.from_bytes(raw).write()


def test_gap_must_be_ff_fill():
    with pytest.raises(ValueError, match="all be 0xFF"):
        GFFWriter(b"PC  ", b"MESH", b"V0.1", b"\x00\xff")


def test_header_fields_must_be_4_bytes():
    with pytest.raises(ValueError, match="platform must be 4 bytes"):
        GFFWriter(b"PC", b"MESH", b"V0.1")
