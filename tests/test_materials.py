from __future__ import annotations

import struct

import pytest

from thedas_io.materials import (
    parse_mao,
    parse_mao_bytes,
    role_of,
    semantic_role,
    slot_roles,
    thedas_io_normals_to_rgba,
    write_mao,
)

MAO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<MaterialObject Name="PN_HAR_Morri">
\t<Material Name="HairAlpha.mat"></Material>
\t<DefaultSemantic Name="Default"></DefaultSemantic>
\t<Texture Name="mml_tPackedTexture" ResName="uh_har_Morri.tga"></Texture>
\t<Texture Name="mml_tTintMask" ResName="Default_White.dds"></Texture>
\t<Texture Name="mml_tTintNoise" ResName="uh_har_0t.dds"></Texture>
\t<Vector4f Name="mml_vHairParameters" value="0.01 60 62 7.32"></Vector4f>
</MaterialObject>"""


def test_role_of():
    assert role_of("c_deera_0d.dds") == "diffuse"
    assert role_of("c_deera_0n.dds") == "normal"
    assert role_of("c_deera_0s.dds") == "specular"
    assert role_of("c_deera_0t.dds") == "tint"
    assert role_of("c_deera_0dl2.dds") == "diffuse"
    assert role_of("wall_0h.tga") == "height"
    assert role_of("nope.dds") is None


def test_role_of_two_digit_lod():
    # _01d is a two-digit LOD, not a new suffix (p_anvil_01d.dds).
    assert role_of("p_anvil_01d.dds") == "diffuse"
    assert role_of("p_anvil_01n.dds") == "normal"
    assert role_of("p_anvil_01s.dds") == "specular"
    assert role_of("x_100t.dds") == "tint"


def test_role_of_game_emissive_suffix():
    # mml_tEmissiveMask ships _0i (pn_arm_dwpd_0i.dds), not only _e.
    assert role_of("pn_arm_dwpd_0i.dds") == "emission"
    assert role_of("arm_dwpd_0e.dds") == "emission"


def test_semantic_role():
    # The MAO slot name is authoritative; the file name only backs it up.
    assert semantic_role("mml_tDiffuse") == "diffuse"
    assert semantic_role("mml_tNormalMap") == "normal"
    assert semantic_role("mml_tSpecularMask") == "specular"
    assert semantic_role("mml_tEmissiveMask") == "emission"
    assert semantic_role("mml_tTintMask") == "tint"
    assert semantic_role("mml_tLightmap") == "lightmap"
    assert semantic_role("mml_tReliefMapPalette") == "height"
    assert semantic_role("MaskV") is None
    assert semantic_role("") is None
    # a suffixed name the file convention misses stays resolved via the slot
    assert semantic_role("mml_tDiffuse") == role_of("ace_master_d.dds")


def test_parse_mao():
    parsed = parse_mao(MAO)
    assert parsed["name"] == "PN_HAR_Morri"
    assert parsed["material"] == "HairAlpha.mat"
    assert parsed["semantic"] == "Default"
    assert parsed["textures"] == {
        "mml_tPackedTexture": "uh_har_Morri.tga",
        "mml_tTintMask": "Default_White.dds",
        "mml_tTintNoise": "uh_har_0t.dds",
    }
    assert parsed["vectors"] == {"mml_vHairParameters": [0.01, 60.0, 62.0, 7.32]}


def test_write_mao_roundtrip():
    assert parse_mao(write_mao(parse_mao(MAO))) == parse_mao(MAO)


def test_slot_roles_filename_first_semantic_fallback():
    """A semantic never shadows a filename-resolved slot sharing its role.

    mml_tTintMask (usually Default_White.dds) must not steal "tint" from
    mml_tTintNoise; a suffix-less p_anvil_01d.dds still resolves via its slot.
    """
    from thedas_io.materials import slot_roles

    assert slot_roles(parse_mao(MAO)["textures"]) == {
        "mml_tPackedTexture": "mml_tPackedTexture",
        "mml_tTintMask": "mml_tTintMask",
        "mml_tTintNoise": "tint",
    }
    assert slot_roles({"mml_tDiffuse": "p_anvil_01d.dds", "mml_tNormalMap": "p_anvil_01n.dds"}) == {
        "mml_tDiffuse": "diffuse",
        "mml_tNormalMap": "normal",
    }
    assert slot_roles({"mml_tDiffuse": "", "mml_tNormalMap": "x_n.dds"}) == {
        "mml_tDiffuse": None,
        "mml_tNormalMap": "normal",
    }


def test_thedas_io_normals_to_rgba():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    flat = thedas_io_normals_to_rgba(Image.new("RGBA", (1, 1), (0, 128, 0, 128)))
    assert flat.tobytes() == bytes([128, 127, 255, 255])
    plus_x = thedas_io_normals_to_rgba(Image.new("RGBA", (1, 1), (0, 128, 0, 255)))
    assert plus_x.tobytes() == bytes([255, 127, 128, 255])


def test_thedas_io_normals_read_green():
    # Y comes from green; R/B are redundant (DA2-style orange maps).
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    px = thedas_io_normals_to_rgba(
        Image.fromarray(np.array([[[10, 191, 30, 64]]], dtype=np.uint8), "RGBA")
    )
    assert px.tobytes() == bytes([64, 64, 218, 255])


def test_normal_swizzle_roundtrip():
    # DAO -> displayed -> DAO is the identity (valid R=G=B images).
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    from thedas_io.export import _swizzle_for_encode

    rng = np.random.default_rng(7)
    yr = rng.integers(0, 256, size=(9, 13))
    xr = rng.integers(0, 256, size=(9, 13))
    dao = np.stack([yr, yr, yr, xr], axis=-1).astype(np.uint8)
    back = np.asarray(
        _swizzle_for_encode(thedas_io_normals_to_rgba(Image.fromarray(dao, "RGBA")), "normal")
    ).astype(int)
    assert np.abs(back - dao.astype(int)).max() <= 2


def test_resize_rgba_survives_zero_alpha():
    """Pillow resizes premultiplied: zero alpha would eat the RGB to black.

    DAO diffuse alpha is unused zeros on opaque materials, so a naive
    resize destroys the texture. RGB and alpha travel separately here.
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    from thedas_io.materials import resize_rgba

    rng = np.random.default_rng(9)
    rgb = rng.integers(0, 256, size=(16, 16, 3)).astype("uint8")
    zero_alpha = np.concatenate([rgb, np.zeros((16, 16, 1), "uint8")], -1)
    back = np.asarray(resize_rgba(Image.fromarray(zero_alpha), (32, 32)))
    assert (
        np.abs(
            back[..., :3].astype(int)
            - np.asarray(Image.fromarray(rgb).resize((32, 32), Image.BICUBIC))
        ).max()
        == 0
    )
    assert (back[..., 3] == 0).all()
    same = resize_rgba(Image.fromarray(zero_alpha), (16, 16))
    assert np.array_equal(np.asarray(same), zero_alpha)


def _fake_mat(props, images=()):
    # Blender-material stand-in: custom props + TexImage nodes.
    from types import SimpleNamespace

    nodes = [SimpleNamespace(type="TEX_IMAGE", image=SimpleNamespace(name=n)) for n in images]
    return SimpleNamespace(get=props.get, node_tree=SimpleNamespace(nodes=nodes))


def _mao_item(props, images=(), text=MAO):
    from thedas_io.export import _mao_bytes

    parsed = parse_mao(text)
    item = {
        "file": "x.mao",
        "kind": "mao",
        "model": parsed,
        "raw": text.encode("utf-8"),
        "objects": [_fake_mat(props, images)],
    }
    return _mao_bytes(item)


def _mao_text(props, images=(), text=MAO):
    out = _mao_item(props, images, text)
    assert out is not None
    return out.decode("utf-8")


def test_mao_bytes_untouched_is_none():
    props = {"mml_vHairParameters": [0.01, 60.0, 62.0, 7.32]}
    assert _mao_item(props, ["uh_har_0t.dds"]) is None


def test_mao_bytes_vector_drift_reencodes():
    props = {"mml_vHairParameters": [0.5, 60.0, 62.0, 7.32]}
    out = parse_mao(_mao_text(props))
    assert out["vectors"] == {"mml_vHairParameters": [0.5, 60.0, 62.0, 7.32]}
    assert out["textures"] == parse_mao(MAO)["textures"]  # links untouched


def test_mao_bytes_texture_swap_reencodes():
    props = {"mml_vHairParameters": [0.01, 60.0, 62.0, 7.32]}
    out = parse_mao(_mao_text(props, ["some_0t.dds"]))
    assert out["textures"]["mml_tTintNoise"] == "some_0t.dds"
    assert out["vectors"] == parse_mao(MAO)["vectors"]


def test_mao_bytes_applies_edits_from_every_material():
    # one MAO backs several mesh materials. A tint run used to return on the
    # first live material, dropping a swap or vector edit made through a later
    # one while still writing the tint
    from thedas_io.export import _mao_bytes
    from thedas_io.tnt import NAMES

    parsed = parse_mao(MAO)
    props = {key: list(value) for key, value in parsed["vectors"].items()}
    edited = {**props, "mml_vHairParameters": [0.5, 60.0, 62.0, 7.32]}
    item = {
        "file": "x.mao",
        "kind": "mao",
        "model": parsed,
        "raw": MAO.encode("utf-8"),
        "objects": [
            _fake_mat(props, ["uh_har_0t.dds"]),
            _fake_mat(edited, ["edited_0t.dds"]),
        ],
    }
    tint = {name: (1.0, 0.0, 0.0, 1.0) for name in NAMES}

    out = _mao_bytes(item, tint)
    assert out is not None
    again = parse_mao(out.decode("utf-8"))
    assert again["textures"]["mml_tTintNoise"] == "edited_0t.dds"
    assert again["vectors"]["mml_vHairParameters"] == [0.5, 60.0, 62.0, 7.32]
    assert any(
        tag == "Vector4fArray" and attrs.get("Name") == "mml_vTintMaskColours"
        for tag, attrs in again["extra"]
    )


def test_mao_bytes_missing_prop_and_dead_mat_are_none():
    assert _mao_item({}) is None
    from thedas_io.export import _mao_bytes

    parsed = parse_mao(MAO)
    dead = {
        "file": "x.mao",
        "kind": "mao",
        "model": parsed,
        "raw": MAO.encode("utf-8"),
        "objects": [],
    }
    assert _mao_bytes(dead) is None
    assert (
        _mao_bytes({"file": "x.mao", "kind": "mao", "model": None, "raw": b"", "objects": []})
        is None
    )


# --- unmodelled MAO data must survive a re-encode ---------------------------

PARAMS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<MaterialObject Name="C_ASHWRAITH">
\t<Material Name="Character.mat"></Material>
\t<DefaultSemantic Name="Default"></DefaultSemantic>
\t<Texture Name="mml_tDiffuse" ResName="c_ashwraith_0d.dds"></Texture>
\t<Float Name="mml_fSpecularReflectionMult" value="1.00"></Float>
\t<Vector4f Name="mml_vFalloffParams" value="0.49 11.88 1.03 1.68"></Vector4f>
\t<SoundType Name="mml_iSoundMaterialType" value="1"></SoundType>
\t<Vector4fArray Name="arr" value="1 2; 3 4"></Vector4fArray>
</MaterialObject>"""

PARAMS_PROPS = {"mml_vFalloffParams": [0.49, 11.88, 1.03, 1.68]}


def test_parse_mao_floats_and_extras():
    parsed = parse_mao(PARAMS)
    assert parsed["floats"] == {"mml_fSpecularReflectionMult": 1.0}
    assert parsed["extra"] == [
        ("SoundType", {"Name": "mml_iSoundMaterialType", "value": "1"}),
        ("Vector4fArray", {"Name": "arr", "value": "1 2; 3 4"}),
    ]


def test_mao_bytes_untouched_params_is_none():
    assert _mao_item(dict(PARAMS_PROPS), text=PARAMS) is None


def test_mao_bytes_vector_drift_keeps_floats_and_extras():
    # Re-encoding for one edited vector must not drop Float/SoundType/arrays.
    props = {"mml_vFalloffParams": [0.5, 11.88, 1.03, 1.68]}
    out = parse_mao(_mao_text(props, text=PARAMS))
    assert out["vectors"] == props
    assert out["floats"] == parse_mao(PARAMS)["floats"]
    assert out["extra"] == parse_mao(PARAMS)["extra"]


def _mao_gff4(
    textures=None,
    *,
    ints=None,
    floats=None,
    vectors=None,
    material="Character.mat",
    semantic="Default",
    extra_tex=None,
):
    # minimal da2 mao: root "mao " plus whichever of its four arrays is given.
    # extra_tex plants a cell the writer does not model, e.g. (33019, 7).
    from thedas_io.gff40.description import FLAGS, GFFStructDef, GFFWriter

    def fourcc(text):
        return int.from_bytes(text.encode("ascii"), "little")

    arrays = [
        (33015, b"int ", 33000, 33001, ints or {}, None),
        (33016, b"flot", 33002, 33003, floats or {}, None),
        (33017, b"flt4", 33004, 33005, vectors or {}, None),
        (33020, b"tex ", 33010, 33011, textures or {}, extra_tex),
    ]
    present = [a for a in arrays if a[4]]
    root_size = 4 * (2 + len(present))

    writer = GFFWriter(b"PC  ", b"MAO ", b"V4.0")
    root = writer.alloc(b"\x00" * root_size)  # root instance must sit at block 0
    writer.struct_defs.append(GFFStructDef(fourcc("mao "), 0, 0, root_size))
    for _, name, _, _, _, extra in present:
        size = 32 if name == b"flt4" else (12 if extra else 8)
        writer.struct_defs.append(GFFStructDef(fourcc(name.decode()), 0, 0, size))

    struct.pack_into("<I", writer.data_block, root, writer.alloc_ecstring(material))
    struct.pack_into("<I", writer.data_block, root + 4, writer.alloc_ecstring(semantic))
    writer.add_field(0, 33012, 14, FLAGS["NONE"], 0)
    writer.add_field(0, 33013, 14, FLAGS["NONE"], 4)

    for index, (label, name, name_label, value_label, values, extra) in enumerate(present, start=1):
        size = writer.struct_defs[index].struct_size
        rows = []
        for key, value in values.items():
            raw = bytearray(size)
            name_ref = writer.alloc_ecstring(str(key))
            if name == b"flt4":
                # value at 0, name at 16, tail padded like the shipped files
                raw[0:16] = struct.pack("<4f", *(float(v) for v in value))
                struct.pack_into("<I", raw, 16, name_ref)
            else:
                struct.pack_into("<I", raw, 0, name_ref)
                if name == b"tex ":
                    struct.pack_into("<I", raw, 4, writer.alloc_ecstring(str(value)))
                    if extra:
                        struct.pack_into("<i", raw, 8, extra[1])
                elif name == b"int ":
                    struct.pack_into("<i", raw, 4, value)
                else:
                    struct.pack_into("<f", raw, 4, value)
            rows.append(bytes(raw))
        block = struct.pack("<I", len(rows)) + b"".join(rows)
        cell = 4 * (2 + index - 1)
        struct.pack_into("<I", writer.data_block, root + cell, writer.alloc(block))
        writer.add_field(0, label, index, FLAGS["STRUCT_ARRAY"], cell)

        if extra:
            writer.add_field(index, extra[0], 5, FLAGS["NONE"], 8)
        if name == b"flt4":
            writer.add_field(index, value_label, 12, FLAGS["NONE"], 0)
            writer.add_field(index, name_label, 14, FLAGS["NONE"], 16)
        elif name == b"tex ":
            writer.add_field(index, name_label, 14, FLAGS["NONE"], 0)
            writer.add_field(index, value_label, 14, FLAGS["NONE"], 4)
        elif name == b"int ":
            writer.add_field(index, name_label, 14, FLAGS["NONE"], 0)
            writer.add_field(index, value_label, 5, FLAGS["NONE"], 4)
        else:
            writer.add_field(index, name_label, 14, FLAGS["NONE"], 0)
            writer.add_field(index, value_label, 8, FLAGS["NONE"], 4)
    return writer.write()


def test_parse_mao_bytes_rejects_non_utf8_and_broken_xml():
    # a corrupt MAO must surface as a per-file ValueError, not a
    # UnicodeDecodeError or ElementTree.ParseError escaping the importer
    with pytest.raises(ValueError, match="not UTF-8"):
        parse_mao_bytes(b"\xff\xfe\x00junk")
    with pytest.raises(ValueError, match="not valid XML"):
        parse_mao_bytes(b"<MaterialObject")


def test_parse_mao_bytes_reads_da2_gff4_textures():
    textures = {
        "mml_tDiffuse": "art\\characters\\playercharacter\\humanmale\\textures\\hm_arm_flta_0d.dds",
        "mml_tNormalMap": "art\\characters\\playercharacter\\humanmale\\textures\\hm_arm_flta_0n.dds",
    }
    parsed = parse_mao_bytes(_mao_gff4(textures))
    assert parsed["textures"] == textures
    assert parsed["binary"] is True
    assert slot_roles(parsed["textures"]) == {
        "mml_tDiffuse": "diffuse",
        "mml_tNormalMap": "normal",
    }


def test_parse_mao_bytes_reads_da2_ints_floats_vectors():
    raw = _mao_gff4(
        ints={"mml_iSoundMaterialType": 2},
        floats={"mml_fSpecularReflectionMult": 0.5},
        vectors={"mml_vUVScrollSpeed": [1.0, 2.0, 0.0, 0.0]},
    )
    parsed = parse_mao_bytes(raw)
    assert parsed["ints"] == {"mml_iSoundMaterialType": 2}
    assert parsed["floats"] == {"mml_fSpecularReflectionMult": 0.5}
    assert parsed["vectors"] == {"mml_vUVScrollSpeed": [1.0, 2.0, 0.0, 0.0]}
    assert parsed["material"] == "Character.mat"
    assert parsed["semantic"] == "Default"


def test_write_mao_binary_untouched_returns_raw():
    from thedas_io.materials import write_mao_binary

    raw = _mao_gff4(
        {"mml_tDiffuse": "a_0d.dds"},
        vectors={"mml_vUVScrollSpeed": [1.0, 0.0, 0.0, 0.0]},
    )
    assert write_mao_binary(parse_mao_bytes(raw), raw) == raw


def test_write_mao_binary_patches_ints_and_floats():
    from thedas_io.materials import write_mao_binary

    raw = _mao_gff4(
        ints={"mml_iSoundMaterialType": 0},
        floats={"mml_fSpecularReflectionMult": 1.0},
    )
    parsed = parse_mao_bytes(raw)
    parsed["ints"]["mml_iSoundMaterialType"] = 2
    parsed["floats"]["mml_fSpecularReflectionMult"] = 0.5

    again = parse_mao_bytes(write_mao_binary(parsed, raw))
    assert again["ints"] == {"mml_iSoundMaterialType": 2}
    assert again["floats"] == {"mml_fSpecularReflectionMult": 0.5}


def test_mao_bytes_binary_texture_swap_preserves_unmodelled_cells():
    from thedas_io.export import _mao_bytes
    from thedas_io.gff40.description import GFFReader

    raw = _mao_gff4(
        {"mml_tDiffuse": "a_0d.dds", "mml_tNormalMap": "a_0n.dds"},
        extra_tex=(33019, 7),
    )
    item = {
        "file": "x.mao",
        "kind": "mao",
        "model": parse_mao_bytes(raw),
        "raw": raw,
        "objects": [_fake_mat({}, ["b_0d.dds", "a_0n.dds"])],
    }
    out = _mao_bytes(item)
    assert out is not None and out != raw

    again = parse_mao_bytes(out)
    assert again["textures"] == {
        "mml_tDiffuse": "b_0d.dds",
        "mml_tNormalMap": "a_0n.dds",
    }
    # the cell the writer does not model came through untouched
    assert GFFReader(out).get_from_data_block(0)[33020][0][33019] == 7


def test_mao_bytes_binary_vector_edit_reencodes():
    from thedas_io.export import _mao_bytes

    raw = _mao_gff4(vectors={"mml_vUVScrollSpeed": [1.0, 0.0, 0.0, 0.0]})
    item = {
        "file": "x.mao",
        "kind": "mao",
        "model": parse_mao_bytes(raw),
        "raw": raw,
        "objects": [_fake_mat({"mml_vUVScrollSpeed": [0.0, 2.0, 0.0, 0.0]})],
    }
    out = _mao_bytes(item)
    assert out is not None
    assert parse_mao_bytes(out)["vectors"] == {"mml_vUVScrollSpeed": [0.0, 2.0, 0.0, 0.0]}


def test_mao_bytes_binary_tint_is_refused():
    from thedas_io.export import _mao_bytes

    raw = _mao_gff4({"mml_tTintMask": "a_0t.dds"})
    item = {
        "file": "x.mao",
        "kind": "mao",
        "model": parse_mao_bytes(raw),
        "raw": raw,
        "objects": [_fake_mat({}, ["a_0t.dds"])],
    }
    with pytest.raises(ValueError, match="tints.rim"):
        _mao_bytes(item, {"Diffuse R": (1.0, 0.0, 0.0, 1.0)})


def test_write_mao_binary_refuses_a_row_it_cannot_model():
    from thedas_io.materials import write_mao_binary

    raw = _mao_gff4({"mml_tDiffuse": "a_0d.dds"})
    parsed = parse_mao_bytes(raw)
    parsed["textures"]["mml_tNew"] = "b_0d.dds"
    with pytest.raises(ValueError, match="no row named"):
        write_mao_binary(parsed, raw)
