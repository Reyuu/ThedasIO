"""Tests for selective export: renames, the connected hierarchy, pretty MAOs.

Multiple meshes and hierarchies share one blend file, so exporting one of
them must gather exactly its mmh, msh, maos and textures - and renaming for
an LOD copy must repoint the hierarchy at its renamed mesh.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from conftest import DATA_DIR
from thedas_io.export import (
    ITEMS,
    _field,
    connected,
    export_all,
    roots,
)
from thedas_io.gff40.description import GFFReader, GFFWriter
from thedas_io.materials import parse_mao, write_mao
from thedas_io.mmh.description import MMHReader

STOCK_MMH = DATA_DIR / "c_corspidra_0.mmh"
STOCK_MSH = DATA_DIR / "c_corspidr_0.msh"
STOCK_MAO = DATA_DIR / "pf_arm_hvyc.mao"


@pytest.fixture
def only_item():
    saved = dict(ITEMS)
    ITEMS.clear()
    yield ITEMS
    ITEMS.clear()
    ITEMS.update(saved)


def _mat(name="MAT"):
    return SimpleNamespace(name=name, node_tree=None)


def _obj(mats=()):
    return SimpleNamespace(type="MESH", name="chunk", data=SimpleNamespace(materials=list(mats)))


def _hierarchy(msh_name="a.msh"):
    # mmh d -> msh a -> mao b -> texture c, all linked by name.
    obj = _obj([_mat("MAT")])
    mmh_raw = STOCK_MMH.read_bytes()
    tree = MMHReader(mmh_raw).tree
    tree.values[6005] = msh_name
    mmh = {
        "file": "d.mmh",
        "kind": "mmh",
        "model": tree,
        "raw": mmh_raw,
        "meshes": [obj],
        "hosts": {},
    }
    msh = {
        "file": "a.msh",
        "kind": "msh",
        "model": None,
        "raw": STOCK_MSH.read_bytes(),
        "objects": [(obj, SimpleNamespace())],
    }
    mao_raw = STOCK_MAO.read_bytes()
    model = parse_mao(mao_raw.decode("utf-8"))
    model["name"] = "MAT"
    model["textures"] = {"mml_tDiffuse": "c.dds"}
    mao = {"file": "b.mao", "kind": "mao", "model": model, "raw": mao_raw, "objects": []}
    tex = {"file": "c.dds", "kind": "texture", "model": None, "raw": b"dds", "objects": []}
    return mmh, msh, mao, tex, obj


def test_roots_are_one_row_per_hierarchy(only_item):
    # Importing one armour shows one object, not its seven files.
    mmh, msh, mao, tex, _unused = _hierarchy()
    only_item.update(
        {
            "d.mmh": mmh,
            "a.msh": msh,
            "b.mao": mao,
            "c.dds": tex,
            "solo.msh": {
                "file": "solo.msh",
                "kind": "msh",
                "model": None,
                "raw": b"s",
                "objects": [],
            },
        }
    )
    assert roots() == ["d.mmh", "solo.msh"]


def test_connected_gathers_the_whole_hierarchy(only_item):
    mmh, msh, mao, tex, _unused = _hierarchy()
    only_item.update(
        {
            "d.mmh": mmh,
            "a.msh": msh,
            "b.mao": mao,
            "c.dds": tex,
            "zzz.mmh": {
                "file": "zzz.mmh",
                "kind": "mmh",
                "model": SimpleNamespace(values={}),
                "raw": b"x",
                "meshes": [],
                "hosts": {},
            },
        }
    )
    assert connected({"a.msh"}) == {"d.mmh", "a.msh", "b.mao", "c.dds"}
    assert connected({"d.mmh"}) == {"d.mmh", "a.msh", "b.mao", "c.dds"}
    assert connected({"zzz.mmh"}) == {"zzz.mmh"}
    assert connected({"nope.mmh"}) == set()


def test_connected_matches_names_case_insensitively(only_item):
    mmh, msh, _mao, _tex, _obj = _hierarchy("A.MSH")
    only_item.update({"d.mmh": mmh, "a.msh": msh})
    assert connected({"d.mmh"}) == {"d.mmh", "a.msh"}


def test_rename_writes_the_new_name_and_repoints_the_msh(only_item, tmp_path):
    mmh_raw = STOCK_MMH.read_bytes()
    only_item["c_corspidra_0.mmh"] = {
        "file": "c_corspidra_0.mmh",
        "kind": "mmh",
        "model": MMHReader(mmh_raw).tree,
        "raw": mmh_raw,
        "meshes": [],
        "hosts": {},
        "as": "x_l2.mmh",
    }
    only_item["c_corspidr_0.msh"] = {
        "file": "c_corspidr_0.msh",
        "kind": "msh",
        "model": None,
        "raw": STOCK_MSH.read_bytes(),
        "objects": [],
        "as": "x_l2.msh",
    }
    written, errors = export_all(tmp_path)
    assert errors == []
    assert sorted(written) == ["x_l2.mmh", "x_l2.msh"]
    gff = GFFReader((tmp_path / "x_l2.mmh").read_bytes())
    assert gff.get_from_data_block(0, 0, 6005) == "x_l2.msh"


def _da2_mmh(msh="art\\characters\\c_corspidr_0.msh"):
    # a DA2 hierarchy stores the mesh's internal path, not its file name
    raw = STOCK_MMH.read_bytes()
    gff = GFFReader(raw)
    writer = GFFWriter.from_reader(gff)
    field = _field(gff, 0, 6005)
    offset = writer.alloc_ecstring(msh)
    struct.pack_into("<I", writer.data_block, field.data_index, offset)
    out = writer.write()
    return out, MMHReader(out).tree


def test_da2_path_reference_folds_into_its_hierarchy(only_item):
    # DA2 names its mesh by internal path; the imported msh item is a leaf, so a
    # separately imported msh must not become a second export row
    mmh, msh, _mao, _tex, _obj = _hierarchy("art\\characters\\a.msh")
    only_item.update({"d.mmh": mmh, "a.msh": msh})
    assert roots() == ["d.mmh"]
    assert connected({"d.mmh"}) == {"d.mmh", "a.msh"}
    assert connected({"a.msh"}) == {"d.mmh", "a.msh"}


def test_rename_keeps_a_da2_directory_in_the_reference(only_item, tmp_path):
    mmh_raw, tree = _da2_mmh()
    only_item["c_corspidra_0.mmh"] = {
        "file": "c_corspidra_0.mmh",
        "kind": "mmh",
        "model": tree,
        "raw": mmh_raw,
        "meshes": [],
        "hosts": {},
        "as": "x_l2.mmh",
    }
    only_item["c_corspidr_0.msh"] = {
        "file": "c_corspidr_0.msh",
        "kind": "msh",
        "model": None,
        "raw": STOCK_MSH.read_bytes(),
        "objects": [],
        "as": "x_l2.msh",
    }
    written, _errors = export_all(tmp_path)
    assert sorted(written) == ["x_l2.mmh", "x_l2.msh"]
    assert (tmp_path / "x_l2.msh").is_file()  # top level, no art\characters subtree
    gff = GFFReader((tmp_path / "x_l2.mmh").read_bytes())
    assert gff.get_from_data_block(0, 0, 6005) == "art\\characters\\x_l2.msh"


def test_an_unrenamed_msh_keeps_the_stock_reference(only_item, tmp_path):
    mmh_raw = STOCK_MMH.read_bytes()
    only_item["c_corspidra_0.mmh"] = {
        "file": "c_corspidra_0.mmh",
        "kind": "mmh",
        "model": MMHReader(mmh_raw).tree,
        "raw": mmh_raw,
        "meshes": [],
        "hosts": {},
        "as": "x_l2.mmh",
    }
    written, errors = export_all(tmp_path)
    assert errors == []
    assert written == ["x_l2.mmh"]
    gff = GFFReader((tmp_path / "x_l2.mmh").read_bytes())
    assert gff.get_from_data_block(0, 0, 6005) == "c_corspidr_0.msh"


def test_a_double_rename_reports_and_skips_the_extra(only_item, tmp_path):
    only_item["a.msh"] = {
        "file": "a.msh",
        "kind": "msh",
        "model": None,
        "raw": b"a",
        "objects": [],
        "as": "same.msh",
    }
    only_item["b.mao"] = {
        "file": "b.mao",
        "kind": "mao",
        "model": {},
        "raw": b"b",
        "objects": [],
        "as": "same.msh",
    }
    written, errors = export_all(tmp_path)
    assert written == ["same.msh"]
    assert errors and "renamed twice" in errors[0]
    assert (tmp_path / "same.msh").read_bytes() == b"a"


def test_only_restricts_the_export_to_a_hierarchy(only_item, tmp_path):
    mmh, msh, mao, tex, _unused = _hierarchy()
    msh["objects"] = []  # geometry edit-back needs real Blender state; raw suffices here
    only_item.update(
        {
            "d.mmh": mmh,
            "a.msh": msh,
            "b.mao": mao,
            "c.dds": tex,
            "other.msh": {
                "file": "other.msh",
                "kind": "msh",
                "model": None,
                "raw": b"o",
                "objects": [],
            },
        }
    )
    written, errors = export_all(tmp_path, only=connected({"a.msh"}))
    assert errors == []
    assert sorted(written) == ["a.msh", "b.mao", "c.dds", "d.mmh"]


def test_write_mao_is_pretty_printed(tmp_path):
    text = write_mao(parse_mao(STOCK_MAO.read_bytes().decode("utf-8")))
    assert "\n\t<Material" in text
    assert "\n\t<Texture" in text
    assert text.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    # still the same document, just whitespace
    assert (
        parse_mao(text)["textures"] == parse_mao(STOCK_MAO.read_bytes().decode("utf-8"))["textures"]
    )


class _Chunk:
    # stand-in for msh.Chunk: the resolver only reads name and two cells
    def __init__(self, name, vertex_offset, index_count):
        self.name = name
        self.cells = {8006: vertex_offset, 8002: index_count}


def test_chunks_are_matched_by_identity_not_by_position():
    # A reload can drop the pairs whose object or chunk is gone, so
    # item["objects"] can be shorter than the file's chunk list. Walking it by
    # position then patches chunk A's geometry into chunk B's region.
    from thedas_io.export import _resolve_base_chunks

    file_chunks = [_Chunk("a", 0, 300), _Chunk("b", 900, 120), _Chunk("c", 1500, 60)]
    base = SimpleNamespace(chunks=file_chunks)

    # only b and c survived the reload, and c's entry came first
    planned = [
        ("obj_c", file_chunks[2], None, "patch", None),
        ("obj_b", file_chunks[1], None, "patch", None),
        ("obj_a", file_chunks[0], None, "raw", None),
    ]
    resolved = _resolve_base_chunks(base, planned)

    assert [entry[1] for entry in resolved] == ["obj_c", "obj_b"]
    assert resolved[0][0] is file_chunks[2]
    assert resolved[1][0] is file_chunks[1]


def test_a_chunk_missing_from_the_source_is_an_error_not_a_silent_shift():
    from thedas_io.export import _resolve_base_chunks

    base = SimpleNamespace(chunks=[_Chunk("a", 0, 300)])
    planned = [("obj_x", _Chunk("gone", 0, 300), None, "patch", None)]

    with pytest.raises(ValueError, match="not in the source file"):
        _resolve_base_chunks(base, planned)
