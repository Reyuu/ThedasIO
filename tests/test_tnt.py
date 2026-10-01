"""Tests for the .tnt tint codec and the tint half of the export path.

Rebuilds a real chargen.rim tint byte for byte, and checks that exporting
with tint on writes the .tnt *and* sets the MMH flag the game needs.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from conftest import DATA_DIR
from thedas_io import tnt
from thedas_io.export import (
    ITEMS,
    _group_tint,
    _use_variation_tint,
    export_all,
)
from thedas_io.gff40.description import GFFReader, GFFWriter
from thedas_io.mmh.description import MMHReader

STOCK_TINT = DATA_DIR / "t1_mub_bk1.tnt"
STOCK_MAO = DATA_DIR / "pf_arm_hvyc.mao"
STOCK_MMH = DATA_DIR / "c_corspidra_0.mmh"
TINT_FLAG = 6340


def test_stock_tint_rebuilds_byte_for_byte():
    raw = STOCK_TINT.read_bytes()
    assert tnt.build(tnt.parse(raw)) == raw


def test_layout_is_ten_vector4f_fields():
    raw = tnt.build({})
    assert raw[:8] == b"GFF V4.0"
    assert raw[8:12] == b"PC  "
    assert raw[12:16] == b"GFF "
    assert raw[28:32] == b"GFF "  # struct fourcc
    (data_offset,) = struct.unpack_from("<I", raw, 24)
    count, field_offset, size = struct.unpack_from("<III", raw, 32)
    assert (count, field_offset, size) == (10, 44, 160)
    assert data_offset == 176  # 28 header + 16 struct + 120 fields + 12 gap
    fields = [struct.unpack_from("<III", raw, field_offset + i * 12) for i in range(count)]
    # add_field keeps field defs in label order, as 1274 of the 1307 stock tints do
    assert [f[0] for f in fields] == sorted(label for _, label in tnt.FIELDS)
    assert {f[1] for f in fields} == {12}  # Vector4f
    assert len(raw) == data_offset + size


def test_defaults_are_untinted():
    values = tnt.parse(tnt.build({}))
    assert values == dict(tnt.DEFAULTS)
    assert all(values[n] == (0.0, 0.0, 0.0, 0.0) for n in values if "opacity" in n)
    assert all(values[n] == (1.0, 1.0, 1.0, 1.0) for n in values if "opacity" not in n)


def test_alpha_input_only_for_opacities():
    assert tnt.alpha_input("Diffuse opacity") == "Diffuse opacity A"
    assert tnt.alpha_input("Diffuse R") is None


def test_build_uses_the_values_it_is_given():
    values = tnt.parse(
        tnt.build({"Diffuse R": (0.1, 0.2, 0.3, 0.4), "Specular opacity": (0.5, 0.6, 0.7, 0.8)})
    )
    assert values["Diffuse R"] == pytest.approx((0.1, 0.2, 0.3, 0.4))
    assert values["Specular opacity"] == pytest.approx((0.5, 0.6, 0.7, 0.8))
    assert values["Diffuse G"] == tnt.DEFAULTS["Diffuse G"]


def test_parse_rejects_a_non_tint():
    with pytest.raises(ValueError, match="GFF V4.0"):
        tnt.parse(b"NOPE" + b"\x00" * 332)
    raw = bytearray(tnt.build({}))
    struct.pack_into("<I", raw, 48, 8)  # first field's type id is no longer Vector4f
    with pytest.raises(ValueError, match="Vector4f"):
        tnt.parse(bytes(raw))
    missing = bytearray(tnt.build({}))
    struct.pack_into("<I", missing, 44, 99999)  # a label the layout does not have
    with pytest.raises(ValueError, match="missing"):
        tnt.parse(bytes(missing))


def _side_group(side: str):
    # One DAO Tint group as ui._tint_group leaves it: just that side's sockets.
    inputs = {
        name: SimpleNamespace(default_value=(0.0, 0.0, 0.0, 1.0)) for name in tnt.for_side(side)
    }
    inputs["Tint mask (RGB)"] = SimpleNamespace(default_value=(0.0, 0.0, 0.0, 1.0))
    inputs["Tint mask (A)"] = SimpleNamespace(default_value=0.0)
    inputs[f"{side} opacity A"] = SimpleNamespace(default_value=0.0)
    return SimpleNamespace(
        type="GROUP", inputs=inputs, node_tree=SimpleNamespace(name=tnt.group_name(side))
    )


def test_group_tint_puts_split_alphas_back():
    # A group's opacity colour is RGB; its 4th component is a float beside it.
    node = _side_group("Diffuse")
    node.inputs["Diffuse opacity A"].default_value = 0.75
    values = _group_tint(node)
    assert values["Diffuse opacity"] == (0.0, 0.0, 0.0, 0.75)
    assert set(values) == set(tnt.for_side("Diffuse"))


def test_each_group_reports_only_its_own_side():
    # One group per side: the specular group must not answer for diffuse.
    assert set(_group_tint(_side_group("Specular"))) == set(tnt.for_side("Specular"))
    assert not set(tnt.for_side("Specular")) & set(tnt.for_side("Diffuse"))


def _tint_flag(raw: bytes) -> list:
    gff = GFFReader(raw)
    return [
        gff.decode_scalar(f, base)
        for _, si, base in _mesh_nodes(gff)
        for f in gff.struct_defs[si].field_instances
        if f.label_id == TINT_FLAG
    ]


def _mesh_nodes(gff):
    from thedas_io.export import _gff_paths

    fourcc = int.from_bytes(b"mshh", "little")
    return [
        (p, si, b)
        for p, si, b in _gff_paths(gff, 0, 0)
        if gff.struct_defs[si].type_fourcc == fourcc
    ]


def test_tint_flag_clears_6340():
    # The variation tint must be off or the engine overwrites the MAO values.
    raw = STOCK_MMH.read_bytes()
    assert _tint_flag(raw) == [0]
    writer = GFFWriter.from_bytes(raw)
    assert _use_variation_tint(GFFReader(raw), writer) is False
    assert writer.write() == raw  # already off: nothing to do
    stock = GFFWriter.from_bytes(_flag_set(raw))
    assert _use_variation_tint(GFFReader(_flag_set(raw)), stock) is True
    assert _tint_flag(stock.write()) == [0]


def _flag_set(raw: bytes) -> bytes:
    # The stock mmh with 6340 on, as the engine ships a dyeable model.
    from thedas_io.export import _gff_paths

    gff = GFFReader(raw)
    writer = GFFWriter.from_bytes(raw)
    fourcc = int.from_bytes(b"mshh", "little")
    for _, si, base in _gff_paths(gff, 0, 0):
        if gff.struct_defs[si].type_fourcc != fourcc:
            continue
        for f in gff.struct_defs[si].field_instances:
            if f.label_id == 6340:
                struct.pack_into("<B", writer.data_block, base + f.data_index, 0xFF)
    return writer.write()


def _item_with_tint(tint_values, material_name: str = "PF_ARM_HVYc"):
    # One mesh whose material carries a DAO Tint group per side.
    nodes = []
    for side in tnt.SIDES:
        group = _side_group(side)
        for name, colour in tint_values.items():
            alpha = tnt.alpha_input(name)
            if not name.startswith(f"{side} "):
                continue
            if alpha:
                group.inputs[name].default_value = tuple(colour)[:3] + (1.0,)
                group.inputs[alpha].default_value = float(colour[3])
            else:
                group.inputs[name].default_value = tuple(colour)
        nodes.append(group)
    obj = SimpleNamespace(
        type="MESH",
        data=SimpleNamespace(
            materials=[SimpleNamespace(name=material_name, node_tree=SimpleNamespace(nodes=nodes))]
        ),
    )
    raw = STOCK_MMH.read_bytes()
    item = {
        "file": "m_fake.mmh",
        "kind": "mmh",
        "model": MMHReader(raw).tree,
        "raw": raw,
        "meshes": [obj],
    }
    return item


def _mao_item(name: str = "PF_ARM_HVYc"):
    # The stock ArmourSkinTint MAO, registered the way an import would.
    from thedas_io.materials import parse_mao

    raw = STOCK_MAO.read_bytes()  # bytes, not read_text: that would normalise CRLF
    return {
        "file": "m_fake.mao",
        "kind": "mao",
        "model": parse_mao(raw.decode("utf-8")),
        "raw": raw,
        "objects": [],
    }


def _baked(path) -> dict:
    # The ten tint values a written MAO carries, by tnt name.
    from thedas_io.materials import parse_mao

    extra = parse_mao(path.read_text(encoding="utf-8"))["extra"]
    arrays = [a for tag, a in extra if a.get("Name") == "mml_vTintMaskColours"]
    assert len(arrays) == 1, f"expected one tint array, got {len(arrays)}"
    flat = [float(x) for x in arrays[0]["value"].split()]
    assert len(flat) == 40
    return {name: tuple(flat[i * 4 : i * 4 + 4]) for i, name in enumerate(tnt.NAMES)}


@pytest.fixture
def only_item():
    saved = dict(ITEMS)
    ITEMS.clear()
    yield ITEMS
    ITEMS.clear()
    ITEMS.update(saved)


def test_tint_writes_the_tnt_and_bakes_the_mao(only_item, tmp_path):
    # Both tint paths carry the same values: the .tnt for chargen, the MAO array as fallback.
    values = {"Diffuse R": (0.1, 0.2, 0.3, 0.4), "Specular opacity": (0.5, 0.6, 0.7, 0.8)}
    only_item["m_fake.mmh"] = _item_with_tint(values)
    only_item["m_fake.mao"] = _mao_item()
    written, errors = export_all(tmp_path, tint=True)
    assert (written, errors) == (["m_fake.tnt", "m_fake.mmh", "m_fake.mao"], [])
    assert tnt.parse((tmp_path / "m_fake.tnt").read_bytes())["Diffuse R"] == pytest.approx(
        (0.1, 0.2, 0.3, 0.4)
    )
    baked = _baked(tmp_path / "m_fake.mao")
    assert baked["Diffuse R"] == pytest.approx((0.1, 0.2, 0.3, 0.4))
    assert baked["Specular opacity"] == pytest.approx((0.5, 0.6, 0.7, 0.8))
    assert baked["Diffuse opacity"] == pytest.approx(tnt.DEFAULTS["Diffuse opacity"])
    assert _tint_flag((tmp_path / "m_fake.mmh").read_bytes()) == [0]


@pytest.mark.parametrize(
    "given, expected",
    [
        ("", "m_fake.tnt"),
        ("t3_arm_rlr", "t3_arm_rlr.tnt"),
        ("t3_arm_rlr.tnt", "t3_arm_rlr.tnt"),
        ("T3_Arm_Rlr", "t3_arm_rlr.tnt"),  # the engine requests lower case
        ("  t3_arm_stl  ", "t3_arm_stl.tnt"),
    ],
)
def test_the_tint_name_is_the_one_the_engine_asks_for(only_item, tmp_path, given, expected):
    # A .tnt is reached by resource name, not by sitting beside the model.
    only_item["m_fake.mmh"] = _item_with_tint({"Diffuse R": (0.1, 0.2, 0.3, 0.4)})
    only_item["m_fake.mao"] = _mao_item()
    _written, errors = export_all(tmp_path, tint=True, tint_name=given)
    assert errors == []
    assert (tmp_path / expected).exists()
    assert tnt.parse((tmp_path / expected).read_bytes())["Diffuse R"] == pytest.approx(
        (0.1, 0.2, 0.3, 0.4)
    )


def test_a_renamed_tnt_writes_under_its_new_name(only_item, tmp_path):
    # The rename table's tint row wins over the tint resource field.
    only_item["m_fake.mmh"] = _item_with_tint({"Diffuse R": (0.1, 0.2, 0.3, 0.4)})
    only_item["m_fake.mmh"]["as_tnt"] = "t3_arm_rlr.tnt"
    _written, errors = export_all(tmp_path, tint=True, tint_name="other.tnt")
    assert errors == []
    assert (tmp_path / "t3_arm_rlr.tnt").exists()
    assert not (tmp_path / "other.tnt").exists()
    assert tnt.parse((tmp_path / "t3_arm_rlr.tnt").read_bytes())["Diffuse R"] == pytest.approx(
        (0.1, 0.2, 0.3, 0.4)
    )


def test_the_baked_array_is_in_tnt_field_order(only_item, tmp_path):
    # The MAO array is the .tnt's ten values flattened, same order.
    values = tnt.parse(STOCK_TINT.read_bytes())
    only_item["m_fake.mmh"] = _item_with_tint(values)
    only_item["m_fake.mao"] = _mao_item()
    export_all(tmp_path, tint=True)
    for name in tnt.NAMES:
        assert _baked(tmp_path / "m_fake.mao")[name] == pytest.approx(values[name], abs=1e-5)


def test_baking_twice_replaces_the_array(only_item, tmp_path):
    only_item["m_fake.mmh"] = _item_with_tint({"Diffuse R": (0.9, 0.9, 0.9, 1.0)})
    only_item["m_fake.mao"] = _mao_item()
    export_all(tmp_path, tint=True)
    text = (tmp_path / "m_fake.mao").read_text(encoding="utf-8")
    assert text.count("mml_vTintMaskColours") == 1
    # a second export over the already-baked MAO must not append a second array
    from thedas_io.materials import parse_mao

    parsed = parse_mao(text)
    only_item["m_fake.mao"] = {
        "file": "m_fake.mao",
        "kind": "mao",
        "model": parsed,
        "raw": text.encode("utf-8"),
        "objects": [],
    }
    export_all(tmp_path, tint=True)
    again = (tmp_path / "m_fake.mao").read_text(encoding="utf-8")
    assert again.count("mml_vTintMaskColours") == 1
    assert _baked(tmp_path / "m_fake.mao")["Diffuse R"] == pytest.approx((0.9, 0.9, 0.9, 1.0))


def test_export_without_tint_touches_nothing(only_item, tmp_path):
    only_item["m_fake.mmh"] = _item_with_tint({"Diffuse R": (1.0, 0.0, 0.0, 1.0)})
    only_item["m_fake.mao"] = _mao_item()
    written, errors = export_all(tmp_path)
    assert (written, errors) == (["m_fake.mmh", "m_fake.mao"], [])
    assert (tmp_path / "m_fake.mao").read_bytes() == STOCK_MAO.read_bytes()
    assert (tmp_path / "m_fake.mmh").read_bytes() == STOCK_MMH.read_bytes()


def test_a_untinted_model_reports_instead_of_writing_a_lie(only_item, tmp_path):
    # No DAO Tint group means no tint to write: the resources still export.
    item = _item_with_tint({})
    item["meshes"][0].data.materials[0].node_tree.nodes = []
    only_item["m_fake.mmh"] = item
    only_item["m_fake.mao"] = _mao_item()
    written, errors = export_all(tmp_path, tint=True)
    assert written == ["m_fake.mmh", "m_fake.mao"]
    assert errors and errors[0].startswith("m_fake.mmh:")
    assert (tmp_path / "m_fake.mao").read_bytes() == STOCK_MAO.read_bytes()
    assert (tmp_path / "m_fake.mmh").read_bytes() == STOCK_MMH.read_bytes()


def test_the_header_and_provenance_comment_survive_a_bake(only_item, tmp_path):
    # ElementTree drops the declaration and Material Editor's dc:source stamp.
    only_item["m_fake.mmh"] = _item_with_tint({"Diffuse R": (0.5, 0.5, 0.5, 1.0)})
    only_item["m_fake.mao"] = _mao_item()
    export_all(tmp_path, tint=True)
    text = (tmp_path / "m_fake.mao").read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    assert "Exported by Material Editor" in text
    assert "dc:source=" in text
    assert text.index("dc:source") < text.index("<MaterialObject")


def test_another_materials_mao_is_left_alone(only_item, tmp_path):
    # The MAO is matched by the material name the MMH uses, not by filename.
    only_item["m_fake.mmh"] = _item_with_tint(
        {"Diffuse R": (0.5, 0.5, 0.5, 1.0)}, material_name="PF_ARM_HVYc"
    )
    other = _mao_item()
    other["file"] = "m_other.mao"
    other["model"] = {**other["model"], "name": "PF_SOMETHINGELSE"}
    only_item["m_other.mao"] = other
    export_all(tmp_path, tint=True)
    assert (tmp_path / "m_other.mao").read_bytes() == STOCK_MAO.read_bytes()
