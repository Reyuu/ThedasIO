"""Structural edits via the GFFWriter data-block allocator (docs/PLAN.md P5).

Appends a node (+trsl/rota children) under Root in c_corspidra_0.mmh:
re-parse asserts tree shape, the edited file self-roundtrips, and the
diff against the source is surgical (one patched ref cell + tail).
"""

from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR, fourcc
from thedas_io.gff40.description import GFFReader, GFFWriter

MMH_PATH = DATA_DIR / "c_corspidra_0.mmh"


def def_index(reader: GFFReader, text: str) -> int:
    for i, sd in enumerate(reader.struct_defs):
        if sd.type_fourcc == fourcc(text):
            return i
    raise AssertionError(f"no {text} def")


def cell(reader: GFFReader, struct_index: int, label_id: int) -> int:
    for f in reader.struct_defs[struct_index].field_instances:
        if f.label_id == label_id:
            return f.data_index
    raise AssertionError(f"no label {label_id} in struct {struct_index}")


def descend(reader: GFFReader, names: list[str]) -> tuple[int, dict]:
    base, value = 0, reader.get_from_data_block(0, 0)
    for name in names:
        entry = next(e for e in value[6999] if (e["value"] or {}).get(6000) == name)
        base, value = entry["offset"], entry["value"]
    return base, value


def append_node_under_root() -> tuple[bytes, bytes, int, int]:
    # Return (raw, edited, kids_cell_file_offset, raw_len).
    raw = MMH_PATH.read_bytes()
    reader = GFFReader(raw)
    writer = GFFWriter.from_reader(reader)
    node_idx, trsl_idx, rota_idx = (def_index(reader, k) for k in ("node", "trsl", "rota"))
    name_cell = cell(reader, node_idx, 6000)
    kids_cell = cell(reader, node_idx, 6999)

    gob = reader.get_from_data_block(0, 0, 6999)[0]
    gob_base, gob_value = gob["offset"], gob["value"]
    trsl_base = next(e["offset"] for e in gob_value[6999] if e["struct_index"] == trsl_idx)
    rota_base = next(e["offset"] for e in gob_value[6999] if e["struct_index"] == rota_idx)
    root_base, root_value = descend(reader, ["GOB", "GOD", "Root"])

    # New children: byte copies of live trsl/rota instances + name string.
    trsl_new = writer.alloc(bytes(writer.data_block[trsl_base : trsl_base + 16]))
    rota_new = writer.alloc(bytes(writer.data_block[rota_base : rota_base + 16]))
    name_new = writer.alloc_ecstring("NewNode")
    kids_new = writer.alloc(
        struct.pack("<I", 2)
        + struct.pack("<HHI", trsl_idx, 0x4000, trsl_new)
        + struct.pack("<HHI", rota_idx, 0x4000, rota_new)
    )
    # New node: GOB instance bytes as template, name/kids cells rewired.
    node_size = reader.struct_defs[node_idx].struct_size
    node_bytes = bytearray(writer.data_block[gob_base : gob_base + node_size])
    struct.pack_into("<I", node_bytes, name_cell, name_new)
    struct.pack_into("<I", node_bytes, kids_cell, kids_new)
    node_new = writer.alloc(bytes(node_bytes))
    # Extend Root's generic CHILDREN list with the new node entry.
    entries = root_value[6999]
    blob = struct.pack("<I", len(entries) + 1)
    for e in entries:
        blob += struct.pack("<HHI", e["struct_index"], e["flags"], e["offset"])
    blob += struct.pack("<HHI", node_idx, 0x4000, node_new)
    root_list_new = writer.alloc(blob)
    # Surgical patch: only Root's 6999 ref cell changes in the old range.
    kids_cell_off = reader.data_offset + root_base + kids_cell
    struct.pack_into("<I", writer.data_block, root_base + kids_cell, root_list_new)
    return raw, writer.write(), kids_cell_off, len(raw)


def test_appended_node_resolves_in_tree():
    _, edited = append_node_under_root()[:2]
    _, root_value = descend(GFFReader(edited), ["GOB", "GOD", "Root"])
    names = [(e["value"] or {}).get(6000) for e in root_value[6999]]
    assert "NewNode" in names
    kids = next(e["value"] for e in root_value[6999] if (e["value"] or {}).get(6000) == "NewNode")[
        6999
    ]
    assert [sorted(e["value"]) for e in kids] == [[6047], [6048]]


def test_edited_file_self_roundtrips():
    from thedas_io.gff40.description import GFFWriter as W

    _, edited = append_node_under_root()[:2]
    assert W.from_bytes(edited).write() == edited


def test_diff_is_surgical():
    raw, edited, kids_cell_off, raw_len = append_node_under_root()
    assert len(edited) > raw_len
    diff = [i for i in range(raw_len) if edited[i] != raw[i]]
    patched = set(range(kids_cell_off, kids_cell_off + 4))
    assert diff and set(diff) <= patched  # only Root's 6999 ref cell changed in the old range


def test_alloc_encoders_land_verbatim():
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    writer = GFFWriter.from_bytes(raw)
    s_off = writer.alloc_ecstring("hi")
    assert bytes(writer.data_block[s_off : s_off + 10]) == struct.pack("<I", 3) + "hi\x00".encode(
        "utf-16-le"
    )
    l_off = writer.alloc_scalar_list(4, [1, 2, 3])
    assert bytes(writer.data_block[l_off : l_off + 16]) == struct.pack("<IIII", 3, 1, 2, 3)
    with pytest.raises(ValueError, match="Not an encodable"):
        writer.alloc_scalar_list(0xFFFF, [1])


def test_add_field_keeps_label_order():
    raw = (DATA_DIR / "c_corspidr_3.msh").read_bytes()
    writer = GFFWriter.from_bytes(raw)
    decl_idx = def_index(GFFReader(raw), "decl")
    writer.add_field(decl_idx, 8029, 4, 0x0000, 99)  # duplicate label sorts past existing
    labels = [f.label_id for f in writer.struct_defs[decl_idx].field_instances]
    assert labels == sorted(labels) and labels.count(8029) == 2
    out = writer.write()
    assert GFFReader(out).struct_defs[decl_idx].field_count == 7
