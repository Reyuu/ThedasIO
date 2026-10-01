"""Tests for GFFReader.get_from_data_block against the real sample files.

Covers: ECString decode, decoded scalars, scalar lists,
inline structs, struct arrays, lists of refs, generic CHILDREN
lists, NULL_REF handling, duplicate-label occurrence selection,
and out-of-range errors.
"""

from __future__ import annotations

import struct

import pytest

from conftest import load
from thedas_io.gff40.description import FLAGS, GFFField


def test_flags_constants():
    assert FLAGS["NONE"] == 0x0000
    assert FLAGS["REFERENCE"] == 0x2000
    assert FLAGS["STRUCT"] == 0x4000
    assert FLAGS["SCALAR_LIST"] == 0x8000
    assert FLAGS["STRUCT_ARRAY"] == 0xC000
    assert FLAGS["LIST_OF_REFS"] == 0xE000
    assert FLAGS["GENERIC_LIST"] == 0xA000


def test_mesh_name_ecstring():
    _, reader = load("c_corspidr_3.msh")
    assert reader.get_from_data_block(0, 0, 2) == "c_corspidr_3.msh"


def test_mesh_name_by_field_object():
    _, reader = load("c_corspidr_3.msh")
    mesh = reader.struct_defs[0]
    assert reader.get_from_data_block(mesh, 0, mesh.field_instances[0]) == "c_corspidr_3.msh"


def test_plain_scalar_decoded():
    _, reader = load("c_corspidr_3.msh")
    assert reader.get_from_data_block(0, 0, 8032) == 0
    assert reader.get_from_data_block(0, 0, 8033) == 0


def test_mesh_root_keys():
    _, reader = load("c_corspidr_3.msh")
    assert sorted(reader.get_from_data_block(0, 0)) == [2, 8021, 8022, 8023, 8032, 8033]


def test_vertex_blob_chaining():
    _, reader = load("c_corspidr_3.msh")
    mesh = reader.get_from_data_block(0, 0)
    assert len(mesh[8022]) == 57216
    assert len(mesh[8023]) == 3952
    # 8022 payload ends exactly where 8023 starts: <I n + n bytes back to back.
    assert 380 + 4 + len(mesh[8022]) == 57600
    assert 57600 + 4 + len(mesh[8023]) == 61556


def test_chunk_list_of_refs():
    _, reader = load("c_corspidr_3.msh")
    chunks = reader.get_from_data_block(0, 0, 8021)
    assert len(chunks) == 1
    assert sorted(chunks[0]) == [
        2,
        8000,
        8001,
        8002,
        8003,
        8004,
        8005,
        8006,
        8007,
        8008,
        8009,
        8011,
        8020,
        8025,
        8034,
    ]


def test_inline_struct_bnds():
    _, reader = load("c_corspidr_3.msh")
    chunk = reader.get_from_data_block(0, 0, 8021)[0]
    assert chunk[8020][8017] == pytest.approx(
        (-0.84619140625, -1.4443359375, 0.01482391357421875, 1.0)
    )


def test_scalar_list_decoded():
    raw, reader = load("c_corspidr_3.msh")
    vals = reader.get_from_data_block(0, 0, 8022)
    (ref,) = struct.unpack_from("<I", raw, reader.data_offset + 12)
    (n,) = struct.unpack_from("<I", raw, reader.data_offset + ref)
    assert len(vals) == n == 57216
    assert vals[:2] == list(struct.unpack_from("<2B", raw, reader.data_offset + ref + 4))


def test_decode_scalar_guards():
    _, reader = load("c_corspidr_3.msh")
    mesh = reader.struct_defs[0]
    with pytest.raises(ValueError, match="inline scalar"):
        reader.decode_scalar(mesh.field_instances[0])
    blob = next(f for f in mesh.field_instances if f.label_id == 8022)
    with pytest.raises(ValueError, match="plain scalar"):
        reader.decode_scalar(blob)


def test_struct_array_decls():
    _, reader = load("c_corspidr_3.msh")
    chunk = reader.get_from_data_block(0, 0, 8021)[0]
    decls = chunk[8025]
    assert len(decls) == 8
    assert decls[0][8028] == 16


def test_null_ref_is_none():
    _, reader = load("c_corspidr_3.msh")
    chunk = reader.get_from_data_block(0, 0, 8021)[0]
    assert chunk[8011] is None


def test_mmh_hierarchy():
    _, reader = load("c_corspidra_0.mmh")
    mdlh = reader.get_from_data_block(0, 0)
    assert mdlh[6000] == "c_corspidra_0.mmh"
    assert [(k["struct_index"], k["offset"]) for k in mdlh[6999]] == [(33, 28)]
    node = mdlh[6999][0]["value"]
    assert node[6000] == "GOB"
    assert [(k["struct_index"], k["offset"]) for k in node[6999]] == [
        (5, 44),
        (5, 104),
        (3, 160),
        (8, 192),
        (7, 208),
        (33, 224),
        (34, 18224),
    ]


def test_phy_root():
    _, reader = load("cai_lrgroofa_0.phy")
    mdlh = reader.get_from_data_block(0, 0)
    assert mdlh[6000] == "cai_lrgroofa_0.mmh"
    kids = mdlh[6999]
    assert [(k["struct_index"], k["offset"]) for k in kids] == [(18, 8)]
    assert kids[0]["value"][6000] == "GOB"


def test_bad_struct_index():
    _, reader = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="out of range"):
        reader.get_from_data_block(99, 0)


def test_unknown_label():
    _, reader = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="not in struct"):
        reader.get_from_data_block(0, 0, 999999)


def test_bad_occurrence():
    _, reader = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="not in struct"):
        reader.get_from_data_block(0, 0, 2, occurrence=1)


def test_instance_out_of_range():
    _, reader = load("c_corspidr_3.msh")
    with pytest.raises(ValueError, match="out of range"):
        reader.get_from_data_block(0, 999999)


def test_generic_field_parses():
    # Type 0xFFFF must survive GFFField construction (mmh/mmh generic lists).
    field = GFFField(6999, 0xFFFF, FLAGS["GENERIC_LIST"], 0)
    assert field.type_id == 0xFFFF
