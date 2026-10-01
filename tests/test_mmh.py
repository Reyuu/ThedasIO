"""Tests for the MMH semantic layer (docs/PLAN.md P2, docs/MMH_GUIDE.md).

Sample: data/c_corspidra_0.mmh — root names, GOB->GOD->Root spine,
Mesh1 refs + bone list, orphan defs, clone roundtrip.
"""

from __future__ import annotations

import pytest

from conftest import DATA_DIR
from thedas_io.mmh.description import MMHReader, MMHWriter, label_name, load
from thedas_io.msh.description import load as msh_load

MMH_PATH = DATA_DIR / "c_corspidra_0.mmh"

ORPHANS = [
    "data",
    "wtrl",
    "bnds",
    "scal",
    "att ",
    "catl",
    "vtdl",
    "msgr",
    "nclt",
    "ntrn",
    "emat",
    "emas",
    "spnv",
    "amel",
    "amap",
    "nemt",
    "spla",
    "nemg",
    "nlpr",
    "nrfl",
    "nirr",
    "nalt",
    "nplt",
    "usrp",
    "snap",
    "emtg",
]


def test_label_names_come_from_labels_table():
    assert label_name(6000) == "GFF_MMH_NAME"
    assert label_name(6999) == "GFF_MMH_CHILDREN"
    assert label_name(999999) == "label_999999"


def test_root_names():
    tree = load(MMH_PATH)
    assert tree.kind == "mdlh"
    assert tree.name == "c_corspidra_0.mmh"
    assert tree.values[6005] == "c_corspidr_0.msh"
    assert (tree.values[6256], tree.values[6275]) == (66, 136)
    assert tree.values[6306] is None


def test_spine():
    tree = load(MMH_PATH)
    gob = tree.find("GOB")
    assert gob.kind == "node"
    god = tree.find("GOD")
    assert god in gob.children
    root = tree.find("Root")
    assert root in god.children
    assert any(c.kind == "node" for c in root.children)


def test_mesh_refs_and_bones():
    mesh1 = load(MMH_PATH).find("Mesh1")
    assert mesh1.kind == "mshh"
    assert mesh1.values[6001] == "c_corspidr"
    chunk = msh_load(DATA_DIR / "c_corspidr_3.msh").chunks[0].name
    assert mesh1.values[6006] == chunk == "C_CORSPIDR_Mesh1"
    assert len(mesh1.values[6255]) == 64
    assert all(isinstance(b, int) for b in mesh1.values[6255])


def test_tree_coverage_and_orphans():
    reader = MMHReader(MMH_PATH.read_bytes())
    assert sum(1 for _ in reader.tree.walk()) == 394
    assert reader.orphan_kinds == ORPHANS
    assert {c.kind for c in reader.tree.walk()} == {
        "mdlh",
        "node",
        "xprt",
        "bbox",
        "trsl",
        "rota",
        "attr",
        "crst",
        "mshh",
    }


def test_find_missing():
    with pytest.raises(KeyError, match="Node 'nope' not found"):
        load(MMH_PATH).find("nope")


def test_clone_roundtrip():
    raw = MMH_PATH.read_bytes()
    assert MMHWriter(MMHReader(raw)).write() == raw


def test_wrong_file_type_rejected():
    with pytest.raises(ValueError, match="Invalid MMH file_type"):
        MMHReader((DATA_DIR / "c_corspidr_3.msh").read_bytes())
