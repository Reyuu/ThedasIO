"""Tests for the ``.phy`` reader and the field-editing exporter.

Uses a real character collision file: two capsules hung off the ``Neck1`` and
``Head`` bones, which is the shape of collision that the toolset tutorial tells
modders to edit by hand.
"""

from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.phy import nxs
from thedas_io.phy.description import PhyReader
from thedas_io.phy.export import add_shapes, phy_bytes, remove_shapes

PHY = DATA_DIR / "qm_uhm_basb_0.phy"


def load_raw():
    return PHY.read_bytes()


def test_remove_shapes_cuts_one_instance_out_of_its_parent():
    # the instance stays in the data block, so every other shape keeps its bytes
    raw = load_raw()
    shapes = PhyReader(raw).shapes
    assert [s.name for s in shapes] == ["HED_Capsule01", "HED_Capsule02"]

    after = remove_shapes(raw, [shapes[0].path])
    kept = PhyReader(after).shapes
    assert [s.name for s in kept] == ["HED_Capsule02"]
    assert kept[0].radius == shapes[1].radius
    assert kept[0].height == shapes[1].height
    assert kept[0].position == shapes[1].position
    assert kept[0].masks == shapes[1].masks


def test_remove_shapes_takes_several_and_reports_nothing_removed():
    raw = load_raw()
    shapes = PhyReader(raw).shapes
    assert PhyReader(remove_shapes(raw, [s.path for s in shapes])).shapes == []
    # a path that is not a shape, and an empty request, leave the file alone
    assert remove_shapes(raw, [(9, 9, 9)]) is None
    assert remove_shapes(raw, []) is None


def test_remove_shapes_works_on_a_file_full_of_meshes():
    # cai_lrgcrna_0 holds two NXS meshes; dropping one must not touch the other
    raw = (DATA_DIR / "cai_lrgcrna_0.phy").read_bytes()
    before = PhyReader(raw).shapes
    after = PhyReader(remove_shapes(raw, [before[0].path])).shapes
    assert [s.name for s in after] == ["Object202"]
    assert after[0].mesh.vertex_count == before[1].mesh.vertex_count
    assert after[0].mesh.raw == before[1].mesh.raw


def test_names_its_model_hierarchy():
    # the toolset tutorial makes renaming MMH_NAME step one of editing a phy, so
    # the reader has to surface it
    assert PhyReader(load_raw()).mmh_name == "qm_uhm_basb_0.mmh"


def test_finds_collision_objects_and_their_bones():
    reader = PhyReader(load_raw())
    assert len(reader.objects) == 2
    assert sorted(o.bone for o in reader.objects) == ["Head", "Neck1"]
    assert all(o.density == 10.0 for o in reader.objects)
    assert all(o.obj_type == 1 for o in reader.objects)


def test_reads_capsule_dimensions():
    shapes = PhyReader(load_raw()).shapes
    assert [s.kind for s in shapes] == ["caps", "caps"]
    assert shapes[0].name == "HED_Capsule01"
    assert round(shapes[0].radius, 5) == 0.08174
    assert round(shapes[0].height, 5) == 0.02513
    assert shapes[0].bone == "Neck1"


def test_reads_the_documented_collision_masks():
    # the four fields the placeable tutorial tells modders to flip
    shape = PhyReader(load_raw()).shapes[0]
    assert shape.masks["placeables"] == 0
    assert shape.masks["static_geometry"] == 0
    assert shape.masks["creatures"] == 1
    assert set(shape.editable) >= {
        "name",
        "position",
        "rotation",
        "radius",
        "height",
        "mask_placeables",
        "mask_static_geometry",
    }


def test_untouched_file_is_byte_identical():
    raw = load_raw()
    assert phy_bytes(raw, None) is None
    assert phy_bytes(raw, {}) is None
    reader = PhyReader(raw)
    shape = reader.shapes[0]
    # an edit that changes nothing must also produce no output
    assert phy_bytes(raw, {shape.path: {"radius": shape.radius}}) is None
    assert phy_bytes(raw, {shape.path: {"mask_placeables": 0}}) is None


def test_the_documented_placeable_edit_round_trips():
    # exactly the four changes from the toolset tutorial
    raw = load_raw()
    shape = PhyReader(raw).shapes[0]
    out = phy_bytes(
        raw,
        {
            shape.path: {
                "mask_placeables": 1,
                "mask_static_geometry": 1,
                "obj_type": 2,
            },
        },
    )
    assert out is not None
    after = PhyReader(out)
    edited = after.shapes[0]
    assert edited.masks["placeables"] == 1
    assert edited.masks["static_geometry"] == 1
    assert after.objects[0].obj_type == 2


def test_capsule_dimensions_and_name_round_trip():
    raw = load_raw()
    shape = PhyReader(raw).shapes[0]
    out = phy_bytes(raw, {shape.path: {"radius": 0.2, "height": 0.15, "name": "RIGGED"}})
    assert out is not None
    after = PhyReader(out).shapes[0]
    assert round(after.radius, 6) == 0.2
    assert round(after.height, 6) == 0.15
    assert after.name == "RIGGED"


def test_position_round_trips():
    raw = load_raw()
    shape = PhyReader(raw).shapes[0]
    out = phy_bytes(raw, {shape.path: {"position": (0.5, 0.0, 0.0, 1.0)}})
    assert out is not None
    after = PhyReader(out).shapes[0]
    assert [round(v, 6) for v in after.position] == [0.5, 0.0, 0.0, 1.0]


def test_editing_one_shape_leaves_the_others_alone():
    raw = load_raw()
    before = PhyReader(raw).shapes
    out = phy_bytes(raw, {before[0].path: {"radius": 0.3}})
    assert out is not None
    after = PhyReader(out).shapes
    assert round(after[0].radius, 6) == 0.3
    assert after[1].radius == before[1].radius
    assert after[1].name == before[1].name
    assert after[1].masks == before[1].masks
    assert after[1].rotation == before[1].rotation


def test_export_is_stable_once_applied():
    raw = load_raw()
    path = PhyReader(raw).shapes[0].path
    once = phy_bytes(raw, {path: {"radius": 0.25}})
    assert once is not None
    # re-applying the same value must not rewrite the file a second time
    assert phy_bytes(once, {PhyReader(once).shapes[0].path: {"radius": 0.25}}) is None


def test_unknown_fields_are_ignored_rather_than_written():
    raw = load_raw()
    path = PhyReader(raw).shapes[0].path
    assert phy_bytes(raw, {path: {"not_a_field": 3}}) is None


def test_the_cooked_mesh_blob_is_left_alone():
    # mesh collision is PhysX-cooked data the engine consumes; nothing in this
    # module may rewrite it
    raw = load_raw()
    path = PhyReader(raw).shapes[0].path
    out = phy_bytes(raw, {path: {"radius": 0.4}})
    assert out is not None
    # the untouched shape's bytes and every other instance survive the rewrite
    assert len(out) > 0
    again = PhyReader(out)
    assert again.summary()["shapes"] == 2


def test_cai_lrgroofa_parses_with_no_collision():
    # a real file whose collision data is empty must still read cleanly
    reader = PhyReader((DATA_DIR / "cai_lrgroofa_0.phy").read_bytes())
    assert reader.mmh_name == "cai_lrgroofa_0.mmh"
    assert reader.shapes == []


@pytest.mark.parametrize("name", ["cai_lrgroofa_0.phy", "qm_uhm_basb_0.phy"])
def test_every_fixture_names_a_model(name):
    assert PhyReader((DATA_DIR / name).read_bytes()).mmh_name


# --- adding shapes ---------------------------------------------------------
#
# GFF struct sizes are fixed in the struct-def table, so a CHILDREN list cannot
# grow in place. It can grow only because the cell is a pointer into the data
# block: a fresh region is written and the cell repointed, leaving every struct
# def untouched.


def test_adding_a_capsule_appends_it_and_keeps_the_rest():
    raw = load_raw()
    before = PhyReader(raw)
    out = add_shapes(
        raw,
        [
            {
                "bone": "Neck1",
                "kind": "capsule",
                "radius": 0.05,
                "height": 0.12,
                "position": (0.02, 0.0, 0.0),
                "name": "ADDED_CAP",
                "masks": {"placeables": 1},
            }
        ],
    )
    assert out is not None
    after = PhyReader(out)
    assert after.summary()["shapes"] == before.summary()["shapes"] + 1

    added = [s for s in after.shapes if s.name == "ADDED_CAP"]
    assert len(added) == 1
    new = added[0]
    assert new.kind == "caps"
    assert new.bone == "Neck1"
    assert round(new.radius, 5) == 0.05
    assert round(new.height, 5) == 0.12
    assert [round(v, 5) for v in new.position[:3]] == [0.02, 0.0, 0.0]
    assert new.masks["placeables"] == 1


def test_adding_leaves_existing_shapes_untouched():
    raw = load_raw()
    before = {s.name: (s.radius, s.height, s.masks, s.position) for s in PhyReader(raw).shapes}
    out = add_shapes(raw, [{"bone": "Head", "kind": "capsule", "radius": 0.2, "height": 0.2}])
    assert out is not None
    after = PhyReader(out)
    assert after.mmh_name == PhyReader(raw).mmh_name
    for shape in after.shapes:
        if shape.name in before:
            r, h, m, p = before[shape.name]
            assert shape.radius == r
            assert shape.height == h
            assert shape.masks == m
            assert shape.position == p


def test_adding_twice_gives_two_shapes():
    raw = load_raw()
    start = PhyReader(raw).summary()["shapes"]
    out = add_shapes(
        raw,
        [
            {"bone": "Neck1", "kind": "capsule", "radius": 0.05, "height": 0.1, "name": "A"},
            {"bone": "Neck1", "kind": "capsule", "radius": 0.06, "height": 0.1, "name": "B"},
        ],
    )
    assert out is not None
    after = PhyReader(out)
    assert after.summary()["shapes"] == start + 2
    names = {s.name for s in after.shapes}
    assert {"A", "B"} <= names


def test_two_additions_with_the_same_name_get_distinct_names():
    # the dedupe has to see names written earlier in the same batch, and those
    # live in the writer's data block rather than in the source file
    raw = load_raw()
    out = add_shapes(
        raw,
        [
            {"bone": "Neck1", "kind": "capsule", "radius": 0.05, "height": 0.1, "name": "DUP"},
            {"bone": "Neck1", "kind": "capsule", "radius": 0.06, "height": 0.1, "name": "DUP"},
        ],
    )
    names = [s.name for s in PhyReader(out).shapes]
    assert names.count("DUP") == 1
    assert "DUP2" in names


def test_added_shape_can_then_be_edited_like_any_other():
    raw = load_raw()
    added = add_shapes(
        raw, [{"bone": "Neck1", "kind": "capsule", "radius": 0.05, "height": 0.1, "name": "TUNE"}]
    )
    shape = next(s for s in PhyReader(added).shapes if s.name == "TUNE")
    out = phy_bytes(added, {shape.path: {"radius": 0.4, "mask_placeables": 1}})
    assert out is not None
    tuned = next(s for s in PhyReader(out).shapes if s.name == "TUNE")
    assert round(tuned.radius, 5) == 0.4
    assert tuned.masks["placeables"] == 1


def test_nothing_to_add_writes_nothing():
    raw = load_raw()
    assert add_shapes(raw, None) is None
    assert add_shapes(raw, []) is None


def test_adding_to_an_unknown_bone_is_an_error():
    with pytest.raises(ValueError, match="no collision object under bone"):
        add_shapes(load_raw(), [{"bone": "NoSuchBone", "kind": "capsule"}])


def test_adding_a_kind_the_file_lacks_is_a_clear_error():
    # this fixture only carries a caps struct, so a box cannot be introduced
    with pytest.raises(ValueError, match="no boxs struct"):
        add_shapes(load_raw(), [{"bone": "Neck1", "kind": "box", "box_dim": (0.1, 0.1, 0.1)}])


def build_nxs(verts, tris, tail=b""):
    """Hand-build an NXS MESH stream the way the DAO install stores them."""
    header = bytearray(36)
    header[:4] = b"NXS\x01"
    header[4:8] = b"MESH"
    struct.pack_into("<I", header, 8, 1)
    struct.pack_into("<I", header, 12, 10)
    struct.pack_into("<f", header, 16, 0.001014)
    struct.pack_into("<I", header, 20, 255)
    struct.pack_into("<I", header, 24, 0)
    struct.pack_into("<II", header, 28, len(verts), len(tris))
    positions = struct.pack(f"<{len(verts) * 3}f", *[c for v in verts for c in v])
    if len(verts) <= 256:
        indices = struct.pack(f"<{3 * len(tris)}B", *[i for t in tris for i in t])
    else:
        indices = struct.pack(f"<{3 * len(tris)}H", *[i for t in tris for i in t])
    return bytes(header) + positions + indices + tail


TETRA = (
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
    [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)],
)


def test_nxs_parses_a_mesh():
    from thedas_io.phy.nxs import parse

    verts, tris = TETRA
    mesh = parse(build_nxs(verts, tris))
    assert mesh.vertex_count == 4
    assert mesh.tri_count == 4
    assert mesh.vertices == verts
    assert mesh.tris == tris


def test_nxs_round_trips_byte_for_byte():
    # the whole point is that editing a mesh leaves everything after the
    # arrays untouched, so the re-encode must be indistinguishable
    from thedas_io.phy.nxs import _write

    verts, tris = TETRA
    raw = build_nxs(verts, tris, tail=b"\x00\x01\x02")
    assert _write(verts, tris, raw) == raw


def test_nxs_translation_and_scale():
    from thedas_io.phy.nxs import parse

    verts, tris = TETRA
    mesh = parse(build_nxs(verts, tris))
    moved = mesh.translated((10.0, 0.0, 0.0))
    assert moved.vertices[1] == (11.0, 0.0, 0.0)
    assert len(moved.raw) == len(mesh.raw)
    scaled = mesh.scaled(2.0)
    assert scaled.vertices[1] == (2.0, 0.0, 0.0)
    assert scaled.tri_count == mesh.tri_count


def test_nxs_switches_index_width_above_256_vertices():
    # a byte cannot address vertex 256, so the file widens the index block
    from thedas_io.phy.nxs import parse

    n = 300
    verts = [(float(i), 0.0, 0.0) for i in range(n)]
    tris = [(i, i + 1, i + 2) for i in range(0, n - 2, 3)]
    mesh = parse(build_nxs(verts, tris))
    assert mesh.vertex_count == n
    assert max(i for t in mesh.tris for i in t) == n - 1


def test_nxs_rejects_a_corrupt_stream():
    from thedas_io.phy.nxs import parse

    raw = bytearray(build_nxs(*TETRA))
    struct.pack_into("<I", raw, 28, 99)  # claims 99 verts, has 4
    import pytest

    with pytest.raises(ValueError):
        parse(bytes(raw))


def test_mesh_shape_exposes_its_decoded_mesh():
    reader = PhyReader(load_raw())
    # the capsule fixture has no mesh, but the reader must not crash and the
    # mesh attribute must be None for non-mesh shapes
    assert all(s.mesh is None for s in reader.shapes)


MESH_PHY = DATA_DIR / "cai_lrgcrna_0.phy"


@pytest.mark.parametrize("name", ["qm_uhm_basb_0.phy", "cai_lrgcrna_0.phy", "cai_lrgroofa_0.phy"])
def test_untouched_phy_rewrites_byte_identical(name):
    # import -> export with nothing changed must reproduce the file exactly,
    # including the NXS mesh blobs (cai_lrgcrna_0 has two mesh shapes)
    from thedas_io.gff40.description import GFFReader, GFFWriter

    raw = (DATA_DIR / name).read_bytes()
    assert GFFWriter.from_reader(GFFReader(raw)).write() == raw


def test_mesh_fixture_decodes_its_geometry():
    reader = PhyReader(MESH_PHY.read_bytes())
    meshes = [s.mesh for s in reader.shapes if s.mesh is not None]
    assert [(m.vertex_count, m.tri_count) for m in meshes] == [(28, 52), (16, 28)]


def test_the_accelerator_block_starts_with_its_node_count():
    # node count is t - 1 for 2,252 of 2,283 sampled shapes, then OPC and HBM
    reader = PhyReader(MESH_PHY.read_bytes())
    for shape in reader.shapes:
        mesh = shape.mesh
        info = nxs.accelerator(mesh.raw)
        assert info["nodes"] == mesh.tri_count - 1
        assert info["sections"]["OPC"] < info["sections"]["HBM"]
        assert info["sections"]["HBM"] < info["length"]
        # bits per axis, millimetre quantum
        assert info["bits"] in (8, 10, 18)
        assert info["quantum"] == pytest.approx(0.001, rel=1e-3)


def test_adding_a_mesh_shape_embeds_the_cooked_stream():
    # a mshs shape stores a PhysX stream; cloning a shipped one proves the blob
    # lands in COOKED_DATA_STREAM and parses back as the same mesh
    raw = (DATA_DIR / "cai_lrgcrna_0.phy").read_bytes()
    shipped = next(s for s in PhyReader(raw).shapes if s.kind == "mshs")
    out = add_shapes(
        raw,
        [
            {
                "bone": "GOB",
                "kind": "mesh",
                "name": "TESTMESH",
                "cooked": shipped.mesh.raw,
                "position": (0.0, 0.0, 0.0, 1.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            }
        ],
    )
    added = next(s for s in PhyReader(out).shapes if s.name == "TESTMESH")
    assert added.kind == "mshs"
    assert added.mesh.vertex_count == shipped.mesh.vertex_count
    assert added.mesh.tri_count == shipped.mesh.tri_count


def test_adding_a_mesh_without_a_cooked_stream_is_an_error():
    raw = (DATA_DIR / "cai_lrgcrna_0.phy").read_bytes()
    with pytest.raises(ValueError, match="cooked"):
        add_shapes(raw, [{"bone": "GOB", "kind": "mesh", "name": "NOSTREAM"}])


def test_adding_a_primitive_the_file_defines_but_never_uses():
    # cai_lrgcrna_0.phy defines boxs and has no box instance to copy. The old
    # fallback cloned the mdlh root at data offset 0, which left one of the
    # root's pointers in the primitive's unused tail.
    raw = (DATA_DIR / "cai_lrgcrna_0.phy").read_bytes()
    assert b"boxs" in raw

    out = add_shapes(
        raw, [{"bone": "GOB", "kind": "box", "box_dim": (0.3, 0.4, 0.5), "name": "TESTBOX"}]
    )
    dims = struct.pack("<3f", 0.3, 0.4, 0.5)
    at = out.find(dims)
    assert at > 0
    # boxs is 16 bytes and BOX_DIM is the first 12; the rest must be ours
    assert out[at + 12 : at + 16] == b"\x00\x00\x00\x00"

    reader = PhyReader(out)
    assert reader.mmh_name == "cai_lrgcrna_0.mmh"
    added = next(s for s in reader.shapes if s.name == "TESTBOX")
    assert added.box_dim == pytest.approx((0.3, 0.4, 0.5), abs=1e-6)
