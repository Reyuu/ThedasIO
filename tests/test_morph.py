# character presets: the .mor/.mop reader on shipped files, a da2 preset whose
# node names are ids and whose part slots are empty, and the vertex stride.
# fixtures come out of face.erf: hf_zz_hf_skn_001.mor, hm_cir100_inn_pat.mor,
# hf_hf2.mop (dao) and default_player.mor (da2, saved as da2_default_player.mor).

from __future__ import annotations

import pytest

from conftest import DATA_DIR
from thedas_io.morph.description import (
    L_FLOAT_PARAM,
    L_FLOAT_VALUE,
    L_TEXTURE_NAME,
    L_TEXTURE_PARAM,
    L_VECTOR_PARAM,
    L_VERTEX_COUNT,
    L_VERTEX_DATA,
    MOPReader,
    MorphTexture,
    MORReader,
    _morph_node,
    _target,
    read,
)

MOR = DATA_DIR / "hf_zz_hf_skn_001.mor"
MOR_VERTICES = DATA_DIR / "hm_cir100_inn_pat.mor"
MOP = DATA_DIR / "hf_hf2.mop"
DA2_MOR = DATA_DIR / "da2_default_player.mor"


def test_mor_reads_its_name_parts_and_tints():
    preset = MORReader(MOR.read_bytes())
    assert preset.name == "hf_zz_hf_skn_001"
    assert [part.name for part in preset.parts][:2] == ["HF_UHM_BASa_0", "hf_uem_basa_0"]
    assert len(preset.parts) == 6
    assert [tint.name for tint in preset.tints][:2] == ["t1_skn_001", ""]
    assert len(preset.tints) == 12


def test_mor_node_parameters_keep_their_order_and_shape():
    preset = MORReader(MOR.read_bytes())
    names = [node.name for node in preset.nodes]
    assert names[:3] == ["BeardM3", "HairM2", "BeardM2"]
    face = next(node for node in preset.nodes if node.name == "FaceM1")
    assert face.textures[0].param == "mml_tAgeNormalMap"
    assert face.textures[0].name == "uh_hed_olda_0n.dds"
    hair = next(node for node in preset.nodes if node.name == "HairM1")
    assert hair.vectors[0].param == "g_vFacialHairColour"
    assert tuple(round(v, 2) for v in hair.vectors[0].values[0]) == (0.08, 0.04, 0.05, 1.0)


def test_mor_vertex_arrays_keep_four_floats_a_vertex():
    preset = MORReader(MOR_VERTICES.read_bytes())
    filled = [node for node in preset.nodes if node.target.count]
    assert filled
    node = filled[0]
    assert len(node.target.vertices) == node.target.count
    assert all(len(vertex) == 4 for vertex in node.target.vertices)
    assert preset.summary()["vertices"] == sum(node.target.count for node in preset.nodes)


def test_mor_rejects_a_vertex_array_that_does_not_match_its_count():
    with pytest.raises(ValueError, match="2 vertices need 8 floats, file has 3"):
        _target({L_VERTEX_COUNT: 2, L_VERTEX_DATA: [1.0, 2.0, 3.0]}, "FaceM1")


def test_da2_mor_keeps_node_ids_as_text_and_empty_slots():
    preset = MORReader(DA2_MOR.read_bytes())
    assert preset.name == "default_player"
    assert preset.parts[0].name.endswith("mt_bas_hm")
    assert preset.parts[1].name == ""
    assert [node.name for node in preset.nodes][:3] == ["975379064", "2785505808", "2785505809"]


def test_mop_reads_overrides():
    preset = MOPReader(MOP.read_bytes())
    assert preset.name == "hf2"
    assert len(preset.parts) == 6
    assert preset.material[0].node == "FaceM1"
    assert preset.material[0].param == "mml_vTintMaskColours"
    assert preset.material[0].index == 3
    assert preset.material[0].value == 1.0
    assert preset.material_vectors[0].value[3] == 1.0
    assert preset.textures == [
        MorphTexture(node="FaceM1", param="mml_tAgeNormalMap", name="uh_hed_olda_0n"),
        MorphTexture(node="FaceM1", param="mml_tAgeDiffuseMap", name="uh_hed_olda_0d"),
    ]


def test_mop_without_model_params_reads_an_empty_list():
    assert MOPReader(MOP.read_bytes()).model == []


def test_float_and_texture_params_decode_from_one_node():
    node = _morph_node(
        {
            2: "FaceM1",
            4: [1.0, 2.0, 3.0],
            L_VERTEX_COUNT: 0,
            L_TEXTURE_PARAM: [{2: "mml_tDiffuse", L_TEXTURE_NAME: "uh_hed_fema_0d"}],
            L_VECTOR_PARAM: [],
            L_FLOAT_PARAM: [{2: "mml_fAgeBlendWeight", L_FLOAT_VALUE: 0.25}],
        }
    )
    assert node.position == (1.0, 2.0, 3.0)
    assert node.textures[0].param == "mml_tDiffuse"
    assert node.floats[0].param == "mml_fAgeBlendWeight"
    assert node.floats[0].value == 0.25
    assert node.floats[0].node == "FaceM1"


def test_read_dispatches_on_the_gff_type():
    assert isinstance(read(MOR.read_bytes()), MORReader)
    assert isinstance(read(MOP.read_bytes()), MOPReader)
    with pytest.raises(ValueError, match="not a MOR or MOP file"):
        read((DATA_DIR / "c_doga_0.mmh").read_bytes())


def test_a_mop_is_not_read_as_a_mor():
    with pytest.raises(ValueError, match="not a MOR file"):
        MORReader(MOP.read_bytes())


def test_part_names_resolve_next_to_a_local_preset(monkeypatch):
    # a preset read from disk takes its parts from its own folder first
    from thedas_io.morph import importer as mod

    row = {"package": None, "filename": MOR.name, "ext": ".mor", "path": str(MOR)}
    hit = mod.part_row("HM_UHM_BASa_0", row, None, "DAO", True)
    assert hit is not None
    assert hit["filename"] == "hm_uhm_basa_0.mmh"
    assert hit["path"].endswith("hm_uhm_basa_0.mmh")
    assert mod.part_row("no_such_part", row, None, "DAO", True) is None


def test_da2_part_paths_ask_the_resource_table(monkeypatch):
    from thedas_io.morph import importer as mod

    seen = {}

    def fake_find_resource(name, db_path=None, game=None):
        seen["name"] = name
        return {"filename": "mt_bas_hm.mmh", "ext": ".mmh", "package": "art.erf"}

    monkeypatch.setattr(mod, "find_resource", fake_find_resource)
    hit = mod.part_row(
        r"art\characters\playercharacter\humanmale\mt_bas_hm", None, None, "DA2", False
    )
    assert seen["name"] == "art/characters/playercharacter/humanmale/mt_bas_hm.mmh"
    assert hit["filename"] == "mt_bas_hm.mmh"


def test_tint_slots_resolve_to_tnt_files():
    from thedas_io.morph import importer as mod

    row = {"package": None, "filename": MOR.name, "ext": ".mor", "path": str(MOR)}
    assert mod.tint_path("t1_skn_003", row, None, "DAO", True) == DATA_DIR / "t1_skn_003.tnt"
    assert mod.tint_path("t9_missing_001", row, None, "DAO", True) is None


def test_tint_slots_name_a_tnt_and_its_node():
    from thedas_io.morph.importer import TINT_TARGETS, tint_rows, tint_targets
    from thedas_io.tnt import NAMES, parse

    assert tint_targets("t1_skn_003") == TINT_TARGETS["skn"]
    assert tint_targets("t3_eye_gry") == ("EyesM1",)
    assert "HairM1" in tint_targets("t3_har_dbr")
    assert tint_targets("t9_zzz_001") == ()

    rows = tint_rows(DATA_DIR / "t3_har_dbr.tnt")
    values = parse((DATA_DIR / "t3_har_dbr.tnt").read_bytes())
    assert len(rows) == len(NAMES) == 10
    assert rows[2] == tuple(float(v) for v in values["Diffuse R"])
    assert tuple(round(v, 3) for v in rows[2]) == (0.432, 0.176, 0.08, 1.0)


def test_mop_rows_stay_sparse_so_a_patch_keeps_the_rest():
    from thedas_io.morph.importer import _mop_params

    preset = MOPReader((DATA_DIR / "hm_hm1.mop").read_bytes())
    params = _mop_params(preset)
    rows = params["FaceM1"]["vectors"]["mml_vTintMaskColours"]
    assert sorted(rows) == [8, 9]
    assert rows[8][0] == pytest.approx(0.83, abs=1e-3)


def test_preset_nodes_match_mesh_object_names():
    from thedas_io.morph.importer import _preset_node

    params = {"FaceM1": {}, "HairM1": {}}
    assert _preset_node("HM_UHM_BAS_FaceM1", params) == "FaceM1"
    assert _preset_node("FaceM1", params) == "FaceM1"
    assert _preset_node("HM_HAR_HC3_HairM2", params) is None


def test_texture_overrides_name_a_dds_and_keep_their_role():
    from thedas_io.materials import semantic_role
    from thedas_io.morph.importer import _texture_res

    assert _texture_res("uh_hed_olda_0n") == "uh_hed_olda_0n.dds"
    assert _texture_res("uh_hed_olda_0n.dds") == "uh_hed_olda_0n.dds"
    assert semantic_role("mml_tAgeNormalMap") == "ageNormal"
    assert semantic_role("mml_tDiffuse") == "diffuse"


def test_blend_weights_read_the_sliders_the_toolset_defines():
    # tools/DefaultSettings.xml: mml_fAgeBlendWeight[11] is the skin diffuse
    # "Blend All Override", [23] the normal one
    from thedas_io.morph.importer import AGE_DIFFUSE, AGE_NORMAL, _flat

    rows = MORReader(MOR_VERTICES.read_bytes()).nodes
    face = next(node for node in rows if node.name == "FaceM1")
    age = next(vec.values for vec in face.vectors if vec.param == "mml_fAgeBlendWeight")
    assert _flat(age, AGE_DIFFUSE) == pytest.approx(0.27)
    assert _flat(age, AGE_NORMAL) == pytest.approx(0.22)
    assert AGE_DIFFUSE == 11 and AGE_NORMAL == 23
    # a sparse mop row map reads the same flat index
    assert _flat({2: (0.0, 0.0, 0.0, 0.4)}, 11) == pytest.approx(0.4)


def test_brow_and_tattoo_weights_use_their_own_arrays():
    from thedas_io.morph.importer import _flat

    preset = MORReader(MOR_VERTICES.read_bytes())
    face = next(node for node in preset.nodes if node.name == "FaceM1")
    brow = next(vec.values for vec in face.vectors if vec.param == "g_vFacialHairWeights")
    tattoo = next(vec.values for vec in face.vectors if vec.param == "mml_vTattooParameters")
    assert max(_flat(brow, i) for i in range(8)) == pytest.approx(1.0)
    assert max(_flat(tattoo, i) for i in range(4)) == 0.0
    colour = next(vec.values for vec in face.vectors if vec.param == "g_vFacialHairColour")
    assert tuple(round(v, 2) for v in colour[0][:3]) == (0.16, 0.06, 0.04)
