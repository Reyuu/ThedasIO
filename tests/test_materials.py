from __future__ import annotations

import pytest

from thedas_io.materials import (
    parse_mao,
    role_of,
    semantic_role,
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
