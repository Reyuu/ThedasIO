"""Substance Painter interchange: OBJ writer, unpack plan, normal round trip.

The DAO-specific image math already lives in materials/export and is pinned
here end to end: unpack a stock-layout material, repack it, and the stored
normal channels (X, Y) come back exact. Z is re-derived (DAO never stores
it), so the test asserts on X/Y, not byte equality.
"""

from __future__ import annotations

import pytest

from thedas_io.materials import parse_mao
from thedas_io.substance import (
    MAP_BASECOLOR,
    MAP_GLOSSINESS,
    MAP_NORMAL,
    MAP_SPECULAR,
    obj_text,
    repack_images,
    unpack_images,
    unpack_plan,
)

ARMOUR_MAO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<MaterialObject Name="PN_ARM_Test">
\t<Material Name="Armour.mat"></Material>
\t<DefaultSemantic Name="NoTint"></DefaultSemantic>
\t<Texture Name="mml_tDiffuse" ResName="pn_arm_0d.dds"></Texture>
\t<Texture Name="mml_tNormalMap" ResName="pn_arm_0n.dds"></Texture>
\t<Texture Name="mml_tSpecularMask" ResName="pn_arm_0s.dds"></Texture>
</MaterialObject>"""


def _decoded():
    return {
        "positions": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        "normals": [(0.0, 0.0, 1.0)] * 3,
        "uvs": {0: [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]},
        "triangles": [(0, 1, 2)],
    }


def test_obj_text_names_materials_and_uvs_pass_through():
    out = obj_text([("Mesh1", "MAT1", _decoded())])
    assert "o Mesh1" in out
    assert "usemtl MAT1" in out
    # stored V passes through untouched: decode_chunk already flipped file-V
    # to Blender-V, and Painter reads V=0 at the bottom like Blender does.
    # Flipping here would mirror every texture vertically.
    assert "vt 0 0" in out
    assert "vt 1 0" in out
    assert "f 1/1/1 2/2/2 3/3/3" in out


def test_obj_text_offsets_across_chunks():
    a, b = _decoded(), _decoded()
    out = obj_text([("A", "M", a), ("B", "M", b)])
    assert "o A" in out and "o B" in out
    assert "usemtl M" in out
    assert "f 4/4/4 5/5/5 6/6/6" in out  # second chunk's rows are offset


def test_obj_text_falls_back_to_object_name():
    out = obj_text([("Mesh1", "", _decoded())])
    assert "usemtl Mesh1" in out


def test_obj_text_references_mtl():
    out = obj_text([("Mesh1", "MAT1", _decoded())])
    assert "mtllib model.mtl" in out.splitlines()[1]
    out = obj_text([("Mesh1", "MAT1", _decoded())], mtl_name="custom.mtl")
    assert "mtllib custom.mtl" in out


def test_mtl_text_maps_only_written_stems():
    from thedas_io.substance import mtl_text

    out = mtl_text([("MAT1", {"basecolor", "normal", "specular", "glossiness"})])
    assert "newmtl MAT1" in out
    assert "map_Kd textures/MAT1/basecolor.png" in out
    assert "map_Bump textures/MAT1/normal.png" in out
    assert "map_Ks textures/MAT1/specular.png" in out
    assert "map_d" not in out  # no opacity written: no statement
    assert "glossiness" not in out  # no Wavefront slot: filename only
    # empty material still declares itself so SP keeps the texture set
    out = mtl_text([("MAT2", set())])
    assert "newmtl MAT2" in out
    assert "map_" not in out


def test_unpack_plan_armour():
    plan = unpack_plan(parse_mao(ARMOUR_MAO))
    assert plan["mml_tDiffuse"][0] == MAP_BASECOLOR
    assert plan["mml_tNormalMap"][0] == MAP_NORMAL
    assert plan["mml_tSpecularMask"][0] == MAP_SPECULAR
    assert plan["mml_tDiffuse#alpha"] == ("specular", None)  # opaque: A is spec
    assert plan["mml_tSpecularMask#gloss"] == (MAP_GLOSSINESS, None)


def test_unpack_plan_transparent_alpha_is_opacity():
    text = ARMOUR_MAO.replace('Name="NoTint"', 'Name="AlphaLerpedTint"')
    plan = unpack_plan(parse_mao(text))
    assert plan["mml_tDiffuse#alpha"] == ("opacity", None)


def test_unpack_plan_lightmap_passes_through():
    text = ARMOUR_MAO.replace(
        "</MaterialObject>",
        '\t<Texture Name="mml_tLightmap" ResName="LM.dds"></Texture>\n</MaterialObject>',
    )
    stem, note = unpack_plan(parse_mao(text))["mml_tLightmap"]
    assert stem is None and "untouched" in note


def test_unpack_plan_tint_passes_through():
    # Tint masks drive chargen, not paint: no file, source bytes stand.
    text = ARMOUR_MAO.replace(
        "</MaterialObject>",
        '\t<Texture Name="mml_tTintMask" ResName="x_0t.dds"></Texture>\n</MaterialObject>',
    )
    stem, note = unpack_plan(parse_mao(text))["mml_tTintMask"]
    assert stem is None and "untouched" in note


def test_flat_map_target():
    from thedas_io.substance import flat_map_target

    assert flat_map_target("PF_ARM_HVYc_basecolor.png") == ("PF_ARM_HVYc", "basecolor")
    assert flat_map_target("M_normal.PNG") == ("M", "normal")
    assert flat_map_target("M_BaseColor.png") == ("M", "basecolor")
    # SP's built-in presets name the color map Diffuse, not basecolor
    assert flat_map_target("PF_ARM_HVYa_Diffuse.png") == ("PF_ARM_HVYa", "basecolor")
    # bare <map>.png collects under "" for single-material attribution
    assert flat_map_target("basecolor.png") == ("", "basecolor")
    assert flat_map_target("M_ao.png") is None  # unknown stem falls out
    assert flat_map_target("notes.txt") is None
    assert flat_map_target("M_normal_base.png") is None  # mesh-map copy, not a channel


def test_attribute_bare_single_multi_none():
    from pathlib import Path

    from thedas_io.substance import attribute_bare

    bare = {"basecolor": Path("basecolor.png"), "normal": Path("normal.png")}
    prefixed = {"basecolor": Path("M_basecolor.png")}
    # single material: bare folds in, prefixed wins per map
    merged, warnings = attribute_bare({"": dict(bare), "M": dict(prefixed)}, ["M"])
    assert warnings == []
    assert merged["M"]["basecolor"].name == "M_basecolor.png"
    assert merged["M"]["normal"].name == "normal.png"
    # several materials: bare left out with a loud warning naming the files
    merged, warnings = attribute_bare({"": dict(bare)}, ["A", "B"])
    assert "M" not in merged and "A" not in merged
    assert len(warnings) == 1 and "basecolor.png" in warnings[0]
    # no bare files: passthrough
    have = {"M": dict(prefixed)}
    assert attribute_bare(have, ["M"]) == (have, [])


def test_collect_sp_maps_flat_wins_per_map(tmp_path):
    from thedas_io.substance import collect_sp_maps

    root = tmp_path / "textures"
    mat_dir = root / "MAT"
    mat_dir.mkdir(parents=True)
    (mat_dir / "basecolor.png").write_bytes(b"old")
    (mat_dir / "normal.png").write_bytes(b"old")
    (root / "MAT_basecolor.png").write_bytes(b"new")
    (root / "MAT_ao.png").write_bytes(b"ignored-anyway")
    (root / "junk.png").write_bytes(b"ignored")
    got = collect_sp_maps(root)
    assert set(got) == {"MAT"}
    assert got["MAT"]["basecolor"].name == "MAT_basecolor.png"  # flat wins
    assert got["MAT"]["normal"].name == "normal.png"  # fallback stands
    assert "ao" not in got["MAT"]


def test_sp_map_stems_cover_every_channel_we_read():
    """The file stems SP must write are exactly the ones the import reads.

    (There is no file-based preset to validate: SP presets are binary
    .spexp, configured in the Output Templates tab. The mapping lives in
    the README recipe; this pins the stem vocabulary both sides share.)
    """
    from thedas_io.substance import SP_MAPS

    assert SP_MAPS == frozenset(
        {"basecolor", "opacity", "normal", "specular", "glossiness", "emissive", "height"}
    )


def test_unpack_basecolor_discards_thedas_io_alpha():
    """SP reads PNG alpha as transparency; DAO's alpha is opacity/spec/data.

    Basecolor ships opaque; the meaning travels in a sidecar file instead.
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    rng = np.random.default_rng(23)
    diffuse = Image.fromarray(rng.integers(0, 256, (8, 8, 4)).astype("uint8"))
    assert (np.asarray(diffuse)[..., 3] != 255).any()  # the test needs real alpha
    sp = unpack_images(parse_mao(ARMOUR_MAO), {"mml_tDiffuse": diffuse})
    base = np.asarray(sp["basecolor"])
    assert (base[..., 3] == 255).all()
    assert (base[..., :3] == np.asarray(diffuse)[..., :3]).all()


def test_unpack_transparent_writes_opacity_face_writes_specular():
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    rng = np.random.default_rng(24)
    diffuse = Image.fromarray(rng.integers(0, 256, (8, 8, 4)).astype("uint8"))
    want = np.asarray(diffuse)[..., 3]
    # transparent semantic: alpha becomes opacity.png
    text = ARMOUR_MAO.replace('Name="NoTint"', 'Name="AlphaLerpedTint"')
    sp = unpack_images(parse_mao(text), {"mml_tDiffuse": diffuse})
    assert "opacity" in sp
    assert (np.asarray(sp["opacity"])[..., 0] == want).all()
    # opaque without a spec map (face-style): alpha becomes specular.png
    text = ARMOUR_MAO.replace(
        '\t<Texture Name="mml_tSpecularMask" ResName="pn_arm_0s.dds"></Texture>\n', ""
    )
    sp = unpack_images(parse_mao(text), {"mml_tDiffuse": diffuse})
    assert "specular" in sp and "opacity" not in sp
    assert (np.asarray(sp["specular"])[..., 0] == want).all()
    # opaque WITH a spec map: the alpha is unused data, discarded
    sp = unpack_images(
        parse_mao(ARMOUR_MAO), {"mml_tDiffuse": diffuse, "mml_tSpecularMask": diffuse}
    )
    assert "specular" in sp and "opacity" not in sp


def test_unpack_repack_roundtrip_xy_exact():
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    rng = np.random.default_rng(11)
    diffuse = Image.fromarray(rng.integers(0, 256, (16, 16, 4)).astype("uint8"))
    n_xy = (rng.random((16, 16, 2)) * 0.6 + 0.2) * 255
    nrm = (n_xy / 255.0) * 2.0 - 1.0
    z = np.sqrt(np.maximum(0.0, 1.0 - (nrm**2).sum(-1)))
    std_n = np.stack([n_xy[..., 0], n_xy[..., 1], z * 255.0, np.full((16, 16), 255.0)], -1).astype(
        "uint8"
    )
    from thedas_io.materials import swizzle_for_encode

    thedas_io_n = swizzle_for_encode(Image.fromarray(std_n, "RGBA"), "normal")
    spec = Image.fromarray(rng.integers(0, 256, (16, 16, 4)).astype("uint8"))

    mao = parse_mao(ARMOUR_MAO)
    sp = unpack_images(
        mao, {"mml_tDiffuse": diffuse, "mml_tNormalMap": thedas_io_n, "mml_tSpecularMask": spec}
    )
    back = repack_images(mao, sp)

    # the stored channels survive the SP trip exactly
    got = np.asarray(back["mml_tNormalMap"].convert("RGBA")).astype(int)
    want = np.asarray(thedas_io_n.convert("RGBA")).astype(int)
    assert (got[..., [3, 1]] == want[..., [3, 1]]).all()  # X in A, Y in G
    # diffuse RGB and gloss ride along untouched
    assert (
        np.asarray(back["mml_tDiffuse"].convert("RGBA"))[..., :3] == np.asarray(diffuse)[..., :3]
    ).all()
    assert (
        np.asarray(back["mml_tSpecularMask"].convert("RGBA"))[..., 3] == np.asarray(spec)[..., 3]
    ).all()


def test_repack_preserves_source_alpha_when_unused():
    """Opaque + dedicated spec map: the diffuse alpha is unused data.

    Sources like pf_arm_hvya_0d.dds carry all-zero alpha there; writing the
    specular channel into it instead invents data the game never asked for
    (and shows up as a grayscale alpha on reimport). With sources given,
    the source alpha survives verbatim; without them the old spec-red
    fallback stands so the output stays well-defined.
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    mao = parse_mao(ARMOUR_MAO)  # opaque NoTint + mml_tSpecularMask slot
    rng = np.random.default_rng(5)
    base = Image.fromarray(rng.integers(0, 256, (8, 8, 3)).astype("uint8"))
    spec = Image.fromarray(rng.integers(0, 256, (8, 8, 3)).astype("uint8"))
    source = Image.fromarray(
        np.concatenate(
            [rng.integers(0, 256, (8, 8, 3)).astype("uint8"), np.zeros((8, 8, 1), "uint8")], -1
        )
    )
    sp = {"basecolor": base, "specular": spec}
    sizes = {"mml_tDiffuse": (8, 8), "mml_tSpecularMask": (8, 8)}
    sources = {"mml_tDiffuse": source}
    back = repack_images(mao, sp, True, sizes, sources)
    assert (np.asarray(back["mml_tDiffuse"])[..., 3] == 0).all()
    # face-style (no spec slot at all): the alpha IS the map, still folded
    text = ARMOUR_MAO.replace(
        '\t<Texture Name="mml_tSpecularMask" ResName="pn_arm_0s.dds"></Texture>\n', ""
    )
    back = repack_images(parse_mao(text), sp, True, sizes, sources)
    assert (
        np.asarray(back["mml_tDiffuse"])[..., 3] == np.asarray(spec.convert("RGBA"))[..., 0]
    ).all()


def test_repack_without_sizes_keeps_sp_size():
    """sizes=None is the keep-resolution path: no silent downscale.

    Mixed source sizes (1024 diffuse beside 512 specular) normally pull
    everything down to the slot; with sizes omitted the SP size survives
    on every map, and the preserved source alpha is resampled up to the
    base instead of crashing the fold.
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    mao = parse_mao(ARMOUR_MAO)
    rng = np.random.default_rng(6)
    sp = {
        stem: Image.fromarray(rng.integers(0, 256, (16, 16, 3)).astype("uint8"))
        for stem in ("basecolor", "normal", "specular", "glossiness")
    }
    source = Image.fromarray(
        np.concatenate(
            [rng.integers(0, 256, (8, 8, 3)).astype("uint8"), np.zeros((8, 8, 1), "uint8")], -1
        )
    )
    back = repack_images(mao, sp, True, None, {"mml_tDiffuse": source})
    assert {k: v.size for k, v in back.items()} == {
        "mml_tDiffuse": (16, 16),
        "mml_tNormalMap": (16, 16),
        "mml_tSpecularMask": (16, 16),
    }
    # source alpha (zeros) rode up to SP size instead of breaking the fold
    assert (np.asarray(back["mml_tDiffuse"])[..., 3] == 0).all()
