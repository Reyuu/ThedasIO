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
