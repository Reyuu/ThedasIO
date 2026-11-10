"""Multi-mesh MMH export: one object with several material slots becomes
several mshh nodes/chunks, one per slot, under the same parent.

The split lives in MMHWriter.split_multi_mesh, which patches the file's GFF
in place, so these tests drive it straight from a real sample: read the file,
split, write, and read the result back. No Blender scene is involved.
"""

from __future__ import annotations

from conftest import DATA_DIR
from thedas_io.mmh.description import MMHReader, MMHWriter

# c_corspidra_0 has a single mshh node ("Mesh1" -> C_CORSPIDR_Mesh1)
SINGLE = DATA_DIR / "c_corspidra_0.mmh"
# c_doga_0 has two mshh siblings, so a split has to keep the second one after
# the group it belongs to
TWO = DATA_DIR / "c_doga_0.mmh"

L_MATERIAL = 6001
L_MESH = 6005
L_CHUNK = 6006
L_BBOX_MIN = 6054
L_BBOX_MAX = 6055


def _mshh_nodes(tree):
    return [node for node in tree.walk() if node.kind == "mshh"]


def _split(path, materials, index=0):
    # (source bytes, split bytes) for the mshh node at `index`
    raw = path.read_bytes()
    reader = MMHReader(raw)
    writer = MMHWriter(reader)
    _, offset = writer.node_offsets()[index]
    writer.split_multi_mesh(offset, materials)
    return raw, writer.write()


def _rest_of(node, skip):
    # every field except the ones the split owns: bbox, tint flag and any
    # unknown cells have to survive the copy untouched. Compared by repr, since
    # a real tint colour is (nan, nan, nan, nan) and NaN never equals itself.
    return sorted((k, repr(v)) for k, v in node.values.items() if k not in skip)


def test_untouched_single_material_mmh_exports_byte_identical():
    # A file that was only read and written straight back must not change: the
    # split path is the only thing allowed to touch it.
    raw = SINGLE.read_bytes()
    assert MMHWriter(MMHReader(raw)).write() == raw


def test_one_material_slot_leaves_the_file_byte_identical():
    # One slot means nothing to split, so the file has to come back untouched.
    raw, out = _split(SINGLE, ["c_corspidr"])
    assert out == raw


def test_two_slots_export_as_two_mshh_nodes():
    raw, out = _split(SINGLE, ["c_corspidr", "body_tint"])
    source = _mshh_nodes(MMHReader(raw).tree)
    nodes = _mshh_nodes(MMHReader(out).tree)

    assert len(nodes) == 2, f"expected 2 mshh nodes, got {len(nodes)}"
    first, second = nodes

    # stable, disambiguated names and chunk refs
    assert (first.name, second.name) == ("Mesh1_0", "Mesh1_1")
    assert (first.values[L_CHUNK], second.values[L_CHUNK]) == (
        "C_CORSPIDR_Mesh1_0",
        "C_CORSPIDR_Mesh1_1",
    )

    # per-chunk material: one node per slot, in slot order
    assert (first.values[L_MATERIAL], second.values[L_MATERIAL]) == (
        "c_corspidr",
        "body_tint",
    )

    # the mesh ref stays where it was: on the root, shared by both chunks
    before = MMHReader(raw).tree.values.get(L_MESH)
    after = MMHReader(out).tree.values.get(L_MESH)
    assert after == before, f"mesh ref moved: {before!r} -> {after!r}"

    # the split only rewrites slot fields: bbox, tint flag and the unknown
    # cells the file carries come through on both nodes unchanged
    owned = {L_MATERIAL, L_CHUNK}
    assert _rest_of(first, owned) == _rest_of(source[0], owned)
    assert _rest_of(second, owned) == _rest_of(source[0], owned)


def test_export_hook_splits_a_two_slot_item():
    # the export side addresses the node by name and hands the slots in order
    from types import SimpleNamespace

    from thedas_io.export import _apply_mmh_split

    linked = SimpleNamespace(
        data=SimpleNamespace(
            materials=[
                SimpleNamespace(name="c_corspidr"),
                SimpleNamespace(name="body_tint"),
            ]
        )
    )
    item = {"raw": SINGLE.read_bytes(), "mesh_nodes": [(SimpleNamespace(name="Mesh1"), linked)]}

    out = _apply_mmh_split(item)
    assert out is not None
    assert [node.name for node in _mshh_nodes(MMHReader(out).tree)] == ["Mesh1_0", "Mesh1_1"]

    # nothing to do means no rewritten bytes to hand back
    assert _apply_mmh_split({**item, "mesh_nodes": []}) is None


def test_split_keeps_an_existing_sibling_after_the_group():
    # TWO starts as Mesh1, Mesh2. Splitting Mesh1 must yield Mesh1_0, Mesh1_1,
    # Mesh2 -- the untouched sibling keeps following the group it was after.
    raw, out = _split(TWO, ["C_DOGa", "dog_tint"])
    order = [node.name for node in _mshh_nodes(MMHReader(raw).tree)]
    assert order == ["Mesh1", "Mesh2"], order

    split_order = [node.name for node in _mshh_nodes(MMHReader(out).tree)]
    assert split_order == ["Mesh1_0", "Mesh1_1", "Mesh2"], split_order
