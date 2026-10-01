"""Real-Blender proofs (headless). The hand fake covers fast unit
logic only; anything touching bpy semantics runs here.

Every test here spawns a real `blender --background`, so they are marked
`blender` and cost ~2.5 minutes as a group. Select them with -m blender, and
drop them from a fast inner loop with -m "not blender".
"""

from __future__ import annotations

import textwrap

import pytest

from blender_runner import run

pytestmark = pytest.mark.blender


def test_register_under_real_blender():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            ok = hasattr(bpy.types, "THEDAS_IO_PT_assets")
            ext.unregister()
            return {"registered": ok,
                    "unregistered": not hasattr(bpy.types, "THEDAS_IO_PT_assets")}
        """)
    ) == {"registered": True, "unregistered": True}


def test_material_build():
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.materials import parse_mao
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()
            diffuse = os.path.join(tmp, "t_0d.dds")
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(buf, "DDS")
            open(diffuse, "wb").write(buf.getvalue())
            normal = os.path.join(tmp, "t_0n.dds")
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), (128, 128, 128, 128)).save(buf, "DDS")
            open(normal, "wb").write(buf.getvalue())
            mao = parse_mao('<MaterialObject Name="M"><Material Name="x.mat"></Material>'
                            '<DefaultSemantic Name="Default"></DefaultSemantic>'
                            '<Texture Name="a" ResName="t_0d.dds"></Texture>'
                            '<Texture Name="b" ResName="t_0n.dds"></Texture>'
                            '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')
            images = {"a": ext.ui.load_image(diffuse), "b": ext.ui.load_image(normal)}
            try:
                mat = ext.ui.build_material("M", mao, images, (0.5, 0.5, 0.5, 1.0))
                bsdf = mat.node_tree.nodes["Specular BSDF"]
                linked = sorted(l.to_socket.name for l in mat.node_tree.links)
                out = {"nodes": sorted(n.type for n in mat.node_tree.nodes),
                       "linked": linked,
                       "targets": sorted(o.target for o in mat.node_tree.nodes
                                         if o.type == "OUTPUT_MATERIAL"),
                       "props": list(mat["v"]),
                       "diffuse_cs": images["a"].colorspace_settings.name,
                       "normal_cs": images["b"].colorspace_settings.name,
                       "normal_px": list(images["b"].pixels[:4])}
            finally:
                for m in list(bpy.data.materials):
                    if m.name == "M":
                        bpy.data.materials.remove(m)
                for name in ("t_0d.dds", "t_0n.dds"):
                    img = bpy.data.images.get(name)
                    if img is not None:
                        bpy.data.images.remove(img)
                ext.unregister()
            return out
        """)
    ) == {
        "nodes": [
            "BSDF_PRINCIPLED",
            "EEVEE_SPECULAR",
            "MAPPING",
            "MIX",
            "NORMAL_MAP",
            "OUTPUT_MATERIAL",
            "OUTPUT_MATERIAL",
            "TEX_COORD",
            "TEX_IMAGE",
            "TEX_IMAGE",
        ],
        "linked": [
            "A",
            "Base Color",
            "Base Color",
            "Color",
            "Normal",
            "Normal",
            "Surface",
            "Surface",
            "Vector",
            "Vector",
            "Vector",
        ],
        "targets": ["CYCLES", "EEVEE"],
        "props": [1.0, 2.0, 3.0, 4.0],
        "diffuse_cs": "sRGB",
        "normal_cs": "Non-Color",
        "normal_px": [0.501960813999176, 0.49803924560546875, 1.0, 1.0],
    }


def test_material_channel_packing():
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.materials import parse_mao
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()

            def dds(name, color, alpha_spot=False, blue_spot=False):
                img = Image.new("RGBA", (8, 8), color)
                if alpha_spot:
                    img.load()[0, 0] = (color[0], color[1], color[2], 0)
                if blue_spot:
                    img.load()[1, 1] = (color[0], color[1], 0, color[3])
                buf = io.BytesIO()
                img.save(buf, "DDS")
                path = os.path.join(tmp, name)
                open(path, "wb").write(buf.getvalue())
                return ext.ui.load_image(path)

            d = dds("p_0d.dds", (200, 100, 50, 255), alpha_spot=True)
            n = dds("p_0n.dds", (128, 128, 128, 128))
            s = dds("p_0s.dds", (40, 30, 20, 200))
            t = dds("p_0t.dds", (10, 20, 30, 255))
            e = dds("p_0e.dds", (5, 5, 5, 255))
            h = dds("h_0d.dds", (60, 70, 80, 90), blue_spot=True)

            def summary(mat):
                pairs = sorted((l.from_socket.name, l.to_socket.name)
                               for l in mat.node_tree.links)
                return {"pairs": pairs,
                        "types": sorted(nd.type for nd in mat.node_tree.nodes),
                        "labels": sorted(n.label for n in mat.node_tree.nodes
                                         if n.type == "MIX"),
                        "gamma": sorted(n.inputs["Gamma"].default_value
                                        for n in mat.node_tree.nodes
                                        if n.type == "GAMMA"),
                        "invert_fac": sorted(n.inputs["Fac"].default_value
                                             for n in mat.node_tree.nodes
                                             if n.type == "INVERT"),
                        "blend": mat.blend_method}

            def mao(semantic, *textures):
                tex = "".join(f'<Texture Name="{k}" ResName="{v}"></Texture>'
                              for k, v in textures)
                return parse_mao(
                    f'<MaterialObject Name="M"><Material Name="m.mat"></Material>'
                    f'<DefaultSemantic Name="{semantic}"></DefaultSemantic>{tex}'
                    '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')

            out = {}
            try:
                out["opaque_with_spec"] = summary(ext.ui.build_material(
                    "FULL", mao("ArmourSkinTint", ("d", "p_0d.dds"),
                                              ("n", "p_0n.dds"),
                                              ("s", "p_0s.dds"), ("t", "p_0t.dds"),
                                              ("e", "p_0e.dds")),
                    {"d": d, "n": n, "s": s, "t": t, "e": e}))
                out["face"] = summary(ext.ui.build_material(
                    "FACE", mao("Default", ("d", "p_0d.dds"), ("n", "p_0n.dds")),
                    {"d": d, "n": n}))
                out["blend"] = summary(ext.ui.build_material(
                    "BLEND", mao("AlphaLerpedTint", ("d", "p_0d.dds")),
                    {"d": d}))
                out["punch"] = summary(ext.ui.build_material(
                    "PUNCH", mao("PunchthroughNoTintL2", ("d", "p_0d.dds")),
                    {"d": d}))
                out["pack"] = summary(ext.ui.build_material(
                    "PACK", mao("Default", ("mml_tPackedTexture", "h_0d.dds")),
                    {"mml_tPackedTexture": h}))
            finally:
                for m in list(bpy.data.materials):
                    bpy.data.materials.remove(m)
                for name in ("p_0d.dds", "p_0n.dds", "p_0s.dds", "p_0t.dds",
                             "p_0e.dds", "h_0d.dds"):
                    img = bpy.data.images.get(name)
                    if img is not None:
                        bpy.data.images.remove(img)
                ext.unregister()
            return out
        """)
    ) == {
        "opaque_with_spec": {
            "pairs": [
                ["Alpha", "Color"],
                ["Alpha", "Tint mask (A)"],
                ["Alpha", "Tint mask (A)"],
                ["BSDF", "Surface"],
                ["BSDF", "Surface"],
                ["Color", "Color"],
                ["Color", "Diffuse"],
                ["Color", "Emission Color"],
                ["Color", "Emissive Color"],
                ["Color", "Roughness"],
                ["Color", "Roughness"],
                ["Color", "Specular"],
                ["Color", "Specular"],
                ["Color", "Tint mask (RGB)"],
                ["Color", "Tint mask (RGB)"],
                ["Normal", "Normal"],
                ["Normal", "Normal"],
                ["Result", "Base Color"],
                ["Result", "Base Color"],
                ["Result", "Color"],
                ["UV", "Vector"],
                ["Vector", "Vector"],
                ["Vector", "Vector"],
                ["Vector", "Vector"],
                ["Vector", "Vector"],
                ["Vector", "Vector"],
            ],
            "types": [
                "BSDF_PRINCIPLED",
                "EEVEE_SPECULAR",
                "GAMMA",
                "GROUP",
                "GROUP",
                "INVERT",
                "MAPPING",
                "NORMAL_MAP",
                "OUTPUT_MATERIAL",
                "OUTPUT_MATERIAL",
                "TEX_COORD",
                "TEX_IMAGE",
                "TEX_IMAGE",
                "TEX_IMAGE",
                "TEX_IMAGE",
                "TEX_IMAGE",
            ],
            "labels": [],
            "gamma": [2.5],
            "invert_fac": [0.5],
            "blend": "HASHED",
        },
        "face": {
            "pairs": [
                ["Alpha", "Specular"],
                ["BSDF", "Surface"],
                ["BSDF", "Surface"],
                ["Color", "Base Color"],
                ["Color", "Base Color"],
                ["Color", "Color"],
                ["Normal", "Normal"],
                ["Normal", "Normal"],
                ["UV", "Vector"],
                ["Vector", "Vector"],
                ["Vector", "Vector"],
            ],
            "types": [
                "BSDF_PRINCIPLED",
                "EEVEE_SPECULAR",
                "MAPPING",
                "NORMAL_MAP",
                "OUTPUT_MATERIAL",
                "OUTPUT_MATERIAL",
                "TEX_COORD",
                "TEX_IMAGE",
                "TEX_IMAGE",
            ],
            "labels": [],
            "gamma": [],
            "invert_fac": [],
            "blend": "HASHED",
        },
        "blend": {
            "pairs": [
                ["Alpha", "Color"],
                ["BSDF", "Surface"],
                ["BSDF", "Surface"],
                ["Color", "Alpha"],
                ["Color", "Base Color"],
                ["Color", "Base Color"],
                ["Color", "Transparency"],
                ["UV", "Vector"],
                ["Vector", "Vector"],
            ],
            "types": [
                "BSDF_PRINCIPLED",
                "EEVEE_SPECULAR",
                "INVERT",
                "MAPPING",
                "OUTPUT_MATERIAL",
                "OUTPUT_MATERIAL",
                "TEX_COORD",
                "TEX_IMAGE",
            ],
            "labels": [],
            "gamma": [],
            "invert_fac": [1.0],
            "blend": "BLEND",
        },
        "pack": {
            "pairs": [
                ["Alpha", "Base Color"],
                ["Alpha", "Base Color"],
                ["BSDF", "Surface"],
                ["BSDF", "Surface"],
                ["Blue", "Color"],
                ["Color", "Alpha"],
                ["Color", "Color"],
                ["Color", "Transparency"],
                ["Green", "Specular"],
                ["UV", "Vector"],
                ["Vector", "Vector"],
            ],
            "types": [
                "BSDF_PRINCIPLED",
                "EEVEE_SPECULAR",
                "INVERT",
                "MAPPING",
                "OUTPUT_MATERIAL",
                "OUTPUT_MATERIAL",
                "SEPARATE_COLOR",
                "TEX_COORD",
                "TEX_IMAGE",
            ],
            "labels": [],
            "gamma": [],
            "invert_fac": [1.0],
            "blend": "BLEND",
        },
        "punch": {
            # Punchthrough is Transparent but alpha-tested, not blended
            "pairs": [
                ["Alpha", "Color"],
                ["BSDF", "Surface"],
                ["BSDF", "Surface"],
                ["Color", "Alpha"],
                ["Color", "Base Color"],
                ["Color", "Base Color"],
                ["Color", "Transparency"],
                ["UV", "Vector"],
                ["Vector", "Vector"],
            ],
            "types": [
                "BSDF_PRINCIPLED",
                "EEVEE_SPECULAR",
                "INVERT",
                "MAPPING",
                "OUTPUT_MATERIAL",
                "OUTPUT_MATERIAL",
                "TEX_COORD",
                "TEX_IMAGE",
            ],
            "labels": [],
            "gamma": [],
            "invert_fac": [1.0],
            "blend": "HASHED",
        },
    }


def test_transparency_follows_the_semantic_not_the_alpha():
    """Diffuse alpha is multi-purpose: only a Transparent semantic means opacity.

    Verified against character.mat.xml: all 20 Transparent-render-class
    semantics start with Alpha/Punchthrough/AddvPunchthrough; the other 32 are
    Opaque and ignore the alpha (face-style maps pack the specular map there).
    """
    assert run(
        textwrap.dedent("""\
        def main():
            import sys
            sys.path.insert(0, "src")
            import thedas_io.ui as ui
            transparent = [
                "AlphaArmourSkinTint", "AlphaEmissiveArmourSkinTint",
                "AlphaEmissiveLerpedTint", "AlphaEmissiveMultiplyTint",
                "AlphaEmissiveNoTint", "AlphaLerpedTint", "AlphaMulTintBody",
                "AlphaMultiplyTint", "AlphaNoTint", "AlphaNoTintBody",
                "PunchthroughArmourSkinTintL2", "PunchthroughEmissiveArmourSkinTintL2",
                "PunchthroughEmissiveLerpedTintL2", "PunchthroughEmissiveMultiplyTintL2",
                "PunchthroughEmissiveNoTintL2", "PunchthroughLerpedTintL2",
                "PunchthroughMultiplyTintL2", "PunchthroughNoTintL2",
                "AddvPunchthroughMultiplyTint2S", "AddvPunchthroughMultiplyTint2SL2",
            ]
            opaque = ["ArmourSkinTint", "ArmourSkinTintL2", "ArmourSkinTintL3",
                      "LerpedTint", "LerpedTintL2", "LerpedTintL3", "MultiplyTint",
                      "NoTint", "NoTintL2", "NoTintL3", "Default", "Blend", "Addv",
                      "LOD1", "LOD2", "LOD3", "Hero", "Face", "Weapon"]
            return {
                "transparent": sorted(s for s in transparent
                                      if not ui._transparent_semantic({"semantic": s})),
                "opaque": sorted(s for s in opaque
                                 if ui._transparent_semantic({"semantic": s})),
                "cutout": sorted(s for s in transparent
                                 if not ui._cutout_semantic({"semantic": s})),
            }
        """)
    ) == {
        "transparent": [],
        "opaque": [],
        "cutout": [
            "AlphaArmourSkinTint",
            "AlphaEmissiveArmourSkinTint",
            "AlphaEmissiveLerpedTint",
            "AlphaEmissiveMultiplyTint",
            "AlphaEmissiveNoTint",
            "AlphaLerpedTint",
            "AlphaMulTintBody",
            "AlphaMultiplyTint",
            "AlphaNoTint",
            "AlphaNoTintBody",
        ],
    }


def test_material_uv_flip_mapping():
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.materials import parse_mao
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(buf, "DDS")
            path = os.path.join(tmp, "f_0d.dds")
            open(path, "wb").write(buf.getvalue())
            mao = parse_mao('<MaterialObject Name="M"><Material Name="m.mat"></Material>'
                            '<DefaultSemantic Name="Default"></DefaultSemantic>'
                            '<Texture Name="d" ResName="f_0d.dds"></Texture>'
                            '</MaterialObject>')
            try:
                mat = ext.ui.build_material("M", mao, {"d": ext.ui.load_image(path)})
                mapping = next(n for n in mat.node_tree.nodes if n.type == "MAPPING")
                uv = next(n for n in mat.node_tree.nodes if n.type == "TEX_COORD")
                tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
                uv_link = next(l for l in mat.node_tree.links
                               if l.from_socket.name == "UV")
                vec_links = [l for l in mat.node_tree.links
                             if l.from_socket.name == "Vector"]
                out = {"scale": list(mapping.inputs["Scale"].default_value),
                       "uv_to": [uv_link.to_node.type, uv_link.to_socket.name],
                       "tex_fed": sorted(l.to_node.type for l in vec_links)}
            finally:
                for m in list(bpy.data.materials):
                    bpy.data.materials.remove(m)
                img = bpy.data.images.get("f_0d.dds")
                if img is not None:
                    bpy.data.images.remove(img)
                ext.unregister()
            return out
        """)
    ) == {"scale": [1.0, -1.0, 1.0], "uv_to": ["MAPPING", "Vector"], "tex_fed": ["TEX_IMAGE"]}


def test_zero_alpha_diffuse_renders():
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.materials import parse_mao
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            import mathutils
            from PIL import Image
            tmp = tempfile.mkdtemp()
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), (200, 100, 50, 0)).save(buf, "DDS")
            path = os.path.join(tmp, "z_0d.dds")
            open(path, "wb").write(buf.getvalue())
            mao = parse_mao('<MaterialObject Name="M"><Material Name="m.mat"></Material>'
                            '<DefaultSemantic Name="Default"></DefaultSemantic>'
                            '<Texture Name="d" ResName="z_0d.dds"></Texture>'
                            '</MaterialObject>')
            try:
                img = ext.ui.load_image(path)
                mode = img.alpha_mode
                mat = ext.ui.build_material("M", mao, {"d": img})
                tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
                emit = mat.node_tree.nodes.new("ShaderNodeEmission")
                outn = next(n for n in mat.node_tree.nodes
                            if n.type == "OUTPUT_MATERIAL")
                mat.node_tree.links.new(tex.outputs["Color"], emit.inputs["Color"])
                mat.node_tree.links.new(emit.outputs["Emission"], outn.inputs["Surface"])
                mesh = bpy.data.meshes.new("m")
                obj = bpy.data.objects.new("o", mesh)
                bpy.context.collection.objects.link(obj)
                mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
                                 [], [(0, 1, 2, 3)])
                mesh.update()
                uvlayer = mesh.uv_layers.new(name="UVMap")
                quad = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
                for poly in mesh.polygons:
                    for loop_i, loop_v in zip(poly.loop_indices, quad):
                        uvlayer.data[loop_i].uv = loop_v
                obj.data.materials.append(mat)
                cam = bpy.data.objects.new("c", bpy.data.cameras.new("c"))
                bpy.context.collection.objects.link(cam)
                cam.location = (0.0, -2.5, 0.9)
                cam.rotation_euler = (mathutils.Vector((0, 0, 0.0)) - cam.location
                                      ).to_track_quat("-Z", "Y").to_euler()
                sc = bpy.context.scene
                sc.camera = cam
                sun = bpy.data.objects.new("s", bpy.data.lights.new("s", "SUN"))
                sun.data.energy = 3.0
                bpy.context.collection.objects.link(sun)
                sc.render.engine = "CYCLES"
                sc.cycles.device = "CPU"
                sc.cycles.samples = 1
                sc.render.resolution_x = 32
                sc.render.resolution_y = 32
                sc.render.filepath = os.path.join(tmp, "r.png")
                sc.render.film_transparent = True
                bpy.ops.render.render(write_still=True)
                got = Image.open(os.path.join(tmp, "r.png")).convert("RGBA")
                px = [p for p in got.getdata() if p[3] > 10]
                import statistics as st
                mean = [round(st.mean(c), 1) for c in zip(*px)] if px else []
                brown = len(px) > 200 and mean[0] > 100 and mean[0] > mean[2] + 20
                out = {"mode": mode, "brown": brown}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    ) == {"mode": "CHANNEL_PACKED", "brown": True}


def test_tint_group_wiring():
    # Every tnt value reaches its own point, and the specular side is tinted too.
    got = run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.materials import parse_mao
            from thedas_io.tnt import NAMES
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()

            def dds(name, color):
                img = Image.new("RGBA", (8, 8), color)
                buf = io.BytesIO()
                img.save(buf, "DDS")
                path = os.path.join(tmp, name)
                open(path, "wb").write(buf.getvalue())
                return ext.ui.load_image(path)

            d = dds("g_0d.dds", (200, 100, 50, 255))
            t = dds("g_0t.dds", (10, 20, 30, 255))
            s = dds("g_0s.dds", (90, 90, 90, 255))
            mao = parse_mao(
                '<MaterialObject Name="M"><Material Name="m.mat"></Material>'
                '<DefaultSemantic Name="Default"></DefaultSemantic>'
                '<Texture Name="d" ResName="g_0d.dds"></Texture>'
                '<Texture Name="t" ResName="g_0t.dds"></Texture>'
                '<Texture Name="s" ResName="g_0s.dds"></Texture>'
                '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')
            try:
                mat = ext.ui.build_material("M", mao, {"d": d, "t": t, "s": s})
                pairs = sorted((l.from_socket.name, l.to_socket.name)
                               for l in mat.node_tree.links)

                def read(group):
                    tree = group.node_tree

                    def origin(node):
                        # "node label:socket" - keeps the two Red sockets apart
                        return sorted("%s:%s" % (l.from_node.label, l.from_socket.name)
                                      for l in tree.links if l.to_node == node)

                    return {
                        "name": tree.name,
                        "iface": sorted((s.name, s.socket_type)
                                        for s in tree.interface.items_tree
                                        if hasattr(s, "socket_type")),
                        "defaults": {s.name: [round(x, 2) for x in s.default_value]
                                     for s in tree.interface.items_tree
                                     if hasattr(s, "socket_type") and s.name in NAMES},
                        "alphas": {s.name: round(s.default_value, 2)
                                   for s in tree.interface.items_tree
                                   if getattr(s, "socket_type", "") == "NodeSocketFloat"},
                        "chain": sorted([n.label] + sorted(
                            (l.to_socket.name,
                             "%s:%s" % (l.from_node.label, l.from_socket.name))
                            for l in tree.links if l.to_node == n)
                            for n in tree.nodes if n.type == "MIX"),
                        "levels": sorted([n.label, origin(n)]
                                         for n in tree.nodes if n.type == "MATH"),
                        "seps": sorted((l.from_socket.name, l.to_node.label,
                                        l.to_socket.name)
                                       for l in tree.links
                                       if l.to_node.type == "SEPARATE_COLOR"),
                        "outs": sorted((l.from_socket.name, l.to_socket.name)
                                       for l in tree.links
                                       if l.to_node.type == "GROUP_OUTPUT"),
                    }

                groups = sorted((read(n) for n in mat.node_tree.nodes
                                 if n.type == "GROUP"),
                                key=lambda g: g["name"])
                out = {"pairs": pairs, "groups": groups}
            finally:
                for m in list(bpy.data.materials):
                    bpy.data.materials.remove(m)
                for name in ("g_0d.dds", "g_0t.dds", "g_0s.dds"):
                    img = bpy.data.images.get(name)
                    if img is not None:
                        bpy.data.images.remove(img)
                for g in list(bpy.data.node_groups):
                    bpy.data.node_groups.remove(g)
                ext.unregister()
            return out
        """)
    )
    assert got == {
        "groups": [
            {
                "alphas": {"Diffuse opacity A": 0.0, "Tint mask (A)": 0.0},
                "chain": [
                    [
                        "Diffuse A tint",
                        ["A", "Diffuse B tint:Result"],
                        ["B", ":Diffuse A"],
                        ["Factor", "Diffuse A:Value"],
                    ],
                    [
                        "Diffuse B tint",
                        ["A", "Diffuse G tint:Result"],
                        ["B", ":Diffuse B"],
                        ["Factor", "Diffuse B:Value"],
                    ],
                    [
                        "Diffuse G tint",
                        ["A", "Diffuse R tint:Result"],
                        ["B", ":Diffuse G"],
                        ["Factor", "Diffuse G:Value"],
                    ],
                    [
                        "Diffuse R tint",
                        ["A", ":Diffuse"],
                        ["B", ":Diffuse R"],
                        ["Factor", "Diffuse R:Value"],
                    ],
                ],
                "defaults": {
                    "Diffuse A": [1.0, 1.0, 1.0, 1.0],
                    "Diffuse B": [1.0, 1.0, 1.0, 1.0],
                    "Diffuse G": [1.0, 1.0, 1.0, 1.0],
                    "Diffuse R": [1.0, 1.0, 1.0, 1.0],
                    "Diffuse opacity": [0.0, 0.0, 0.0, 0.0],
                },
                "iface": [
                    ["Diffuse", "NodeSocketColor"],
                    ["Diffuse A", "NodeSocketColor"],
                    ["Diffuse B", "NodeSocketColor"],
                    ["Diffuse G", "NodeSocketColor"],
                    ["Diffuse R", "NodeSocketColor"],
                    ["Diffuse opacity", "NodeSocketColor"],
                    ["Diffuse opacity A", "NodeSocketFloat"],
                    ["Result", "NodeSocketColor"],
                    ["Tint mask (A)", "NodeSocketFloat"],
                    ["Tint mask (RGB)", "NodeSocketColor"],
                ],
                "levels": [
                    ["Diffuse A", [":Diffuse opacity A", ":Tint mask (A)"]],
                    ["Diffuse B", ["Diffuse opacity:Blue", "mask channels:Blue"]],
                    ["Diffuse G", ["Diffuse opacity:Green", "mask channels:Green"]],
                    ["Diffuse R", ["Diffuse opacity:Red", "mask channels:Red"]],
                ],
                "name": "DAO Tint Diffuse",
                "outs": [["Result", "Result"]],
                "seps": [
                    ["Diffuse opacity", "Diffuse opacity", "Color"],
                    ["Tint mask (RGB)", "mask channels", "Color"],
                ],
            },
            {
                "alphas": {"Specular opacity A": 0.0, "Tint mask (A)": 0.0},
                "chain": [
                    [
                        "Specular A tint",
                        ["A", "Specular B tint:Result"],
                        ["B", ":Specular A"],
                        ["Factor", "Specular A:Value"],
                    ],
                    [
                        "Specular B tint",
                        ["A", "Specular G tint:Result"],
                        ["B", ":Specular B"],
                        ["Factor", "Specular B:Value"],
                    ],
                    [
                        "Specular G tint",
                        ["A", "Specular R tint:Result"],
                        ["B", ":Specular G"],
                        ["Factor", "Specular G:Value"],
                    ],
                    [
                        "Specular R tint",
                        ["A", ":Specular"],
                        ["B", ":Specular R"],
                        ["Factor", "Specular R:Value"],
                    ],
                ],
                "defaults": {
                    "Specular A": [1.0, 1.0, 1.0, 1.0],
                    "Specular B": [1.0, 1.0, 1.0, 1.0],
                    "Specular G": [1.0, 1.0, 1.0, 1.0],
                    "Specular R": [1.0, 1.0, 1.0, 1.0],
                    "Specular opacity": [0.0, 0.0, 0.0, 0.0],
                },
                "iface": [
                    ["Result", "NodeSocketColor"],
                    ["Specular", "NodeSocketColor"],
                    ["Specular A", "NodeSocketColor"],
                    ["Specular B", "NodeSocketColor"],
                    ["Specular G", "NodeSocketColor"],
                    ["Specular R", "NodeSocketColor"],
                    ["Specular opacity", "NodeSocketColor"],
                    ["Specular opacity A", "NodeSocketFloat"],
                    ["Tint mask (A)", "NodeSocketFloat"],
                    ["Tint mask (RGB)", "NodeSocketColor"],
                ],
                "levels": [
                    ["Specular A", [":Specular opacity A", ":Tint mask (A)"]],
                    ["Specular B", ["Specular opacity:Blue", "mask channels:Blue"]],
                    ["Specular G", ["Specular opacity:Green", "mask channels:Green"]],
                    ["Specular R", ["Specular opacity:Red", "mask channels:Red"]],
                ],
                "name": "DAO Tint Specular",
                "outs": [["Result", "Result"]],
                "seps": [
                    ["Specular opacity", "Specular opacity", "Color"],
                    ["Tint mask (RGB)", "mask channels", "Color"],
                ],
            },
        ],
        "pairs": [
            ["Alpha", "Color"],
            ["Alpha", "Tint mask (A)"],
            ["Alpha", "Tint mask (A)"],
            ["BSDF", "Surface"],
            ["BSDF", "Surface"],
            ["Color", "Diffuse"],
            ["Color", "Roughness"],
            ["Color", "Roughness"],
            ["Color", "Specular"],
            ["Color", "Specular"],
            ["Color", "Tint mask (RGB)"],
            ["Color", "Tint mask (RGB)"],
            ["Result", "Base Color"],
            ["Result", "Base Color"],
            ["Result", "Color"],
            ["UV", "Vector"],
            ["Vector", "Vector"],
            ["Vector", "Vector"],
            ["Vector", "Vector"],
        ],
    }


def test_import_msh_sample():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.export import ITEMS
            from thedas_io.msh.description import MSHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            raw = open(os.path.join("data", "c_corspidr_3.msh"), "rb").read()
            mesh = MSHReader(raw).mesh
            objs = [ext.ui.build_chunk_object(bpy.context, mesh, c, raw, "c_corspidr_3.msh")
                    for c in mesh.chunks]
            try:
                o = objs[0]
                md = o.data
                stashed = ITEMS.get("c_corspidr_3.msh")
                out = {"verts": len(md.vertices), "tris": len(md.polygons),
                       "uvs": len(md.uv_layers), "groups": len(o.vertex_groups),
                       "mapped": len(list(o.get("thedas_io_row_to_vert") or [])),
                       "stashed": stashed is not None
                       and [ob for ob, _ in stashed["objects"]] == objs}
            finally:
                for o in objs:
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    ) == {"verts": 330, "tris": 656, "uvs": 1, "groups": 64, "mapped": 1100, "stashed": True}


def test_import_mmh_pair():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.mmh.description import MMHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            raw = open(os.path.join("data", "c_corspidra_0.mmh"), "rb").read()
            tree = MMHReader(raw).tree
            from thedas_io.msh.description import MSHReader
            mraw = open(os.path.join("data", "c_corspidr_0.msh"), "rb").read()
            smesh = MSHReader(mraw).mesh
            chunks = {}
            for chunk in smesh.chunks:
                chunks[chunk.name] = ext.ui.build_chunk_object(
                    bpy.context, smesh, chunk, mraw, "c_corspidr_0.msh")
            stats = ext.ui.build_hierarchy(bpy.context, tree, chunks)
            try:
                arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
                bones = arm.data.bones
                gob = bones.get("GOB")
                chain = [gob.name]
                b = gob
                while b.children:
                    b = sorted(b.children, key=lambda x: x.name)[0]
                    chain.append(b.name)
                    if len(chain) > 6:
                        break
                chunk = chunks["C_CORSPIDR_Mesh1"]
                out = {"bones": len(bones), "empties": stats["empties"],
                       "linked": stats["linked"], "missing": stats["missing"],
                       "chain": chain[:4],
                       "parent": chunk.parent.name if chunk.parent else None,
                       "parent_type": chunk.parent_type,
                       "renamed": sorted(g.name for g in chunk.vertex_groups)[:3],
                       "leftover": [g.name for g in chunk.vertex_groups if g.name.startswith("bone_")]}
            finally:
                for o in list(bpy.context.scene.objects):
                    if o.type in ("ARMATURE", "EMPTY") or o.name.startswith(
                            ("c_corspidra", "C_CORSPIDR", "CrustHook", "GOB")):
                        bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    ) == {
        "bones": 68,
        "empties": 16,
        "linked": ["Mesh1"],
        "missing": [],
        "chain": ["GOB", "GOD", "Root", "JSpiderButt01"],
        "parent": "c_corspidra_0.mmh",
        "parent_type": "OBJECT",
        "renamed": ["ArmL01", "ArmL02", "ArmL03"],
        "leftover": [],
    }


def test_import_mmh_skin_mapping():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.geometry import bone_names, decode_chunk
            from thedas_io.mmh.description import MMHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            raw = open(os.path.join("data", "c_corspidra_0.mmh"), "rb").read()
            tree = MMHReader(raw).tree
            from thedas_io.msh.description import MSHReader
            mraw = open(os.path.join("data", "c_corspidr_0.msh"), "rb").read()
            smesh = MSHReader(mraw).mesh
            chunks = {}
            for chunk in smesh.chunks:
                chunks[chunk.name] = ext.ui.build_chunk_object(
                    bpy.context, smesh, chunk, mraw, "c_corspidr_0.msh")
            stats = ext.ui.build_hierarchy(bpy.context, tree, chunks)
            try:
                names = bone_names(tree)
                node = next(n for n in tree.walk() if n.kind == "mshh")
                order = node.values.get(6255) or []
                chunk = smesh.chunks[0]
                obj = chunks[chunk.name]
                from thedas_io.geometry import weld_positions
                build = decode_chunk(smesh, chunk)
                groups = build["groups"]
                row_to_vert = weld_positions(build["positions"])[1]
                n = len(obj.data.vertices)
                step = max(1, n // 150)
                mismatches = []
                checked = 0
                for v in range(0, n, step):
                    members = [(w, b) for b, ms in groups.items()
                               for vv, w in ms if row_to_vert[vv] == v]
                    if not members:
                        continue
                    want = names[order[max(members)[1]]]
                    got = max(((g.weight, obj.vertex_groups[g.group].name)
                               for g in obj.data.vertices[v].groups),
                              default=(0.0, None))[1]
                    checked += 1
                    if got != want:
                        mismatches.append([v, want, got])
                out = {"checked": checked > 50, "mismatches": mismatches[:5]}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    ) == {"checked": True, "mismatches": []}


def test_import_usrp_hooks():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.gff40.description import GFFWriter
            from thedas_io.mmh.description import MMHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from mathutils import Vector
            raw = open(os.path.join("data", "c_corspidra_0.mmh"), "rb").read()
            writer = GFFWriter.from_bytes(raw)
            crst = int.from_bytes(b"crst", "little")
            swapped = 0
            for sd in writer.struct_defs:
                if sd.type_fourcc == crst:
                    sd.type_fourcc = int.from_bytes(b"usrp", "little")
                    swapped += 1
            assert swapped == 1
            uraw = writer.write()
            tree = MMHReader(uraw).tree
            assert all(n.kind != "crst" for n in tree.walk())
            from thedas_io.msh.description import MSHReader
            mraw = open(os.path.join("data", "c_corspidr_0.msh"), "rb").read()
            smesh = MSHReader(mraw).mesh
            chunks = {}
            for chunk in smesh.chunks:
                chunks[chunk.name] = ext.ui.build_chunk_object(
                    bpy.context, smesh, chunk, mraw, "c_corspidr_0.msh")
            stats = ext.ui.build_hierarchy(bpy.context, tree, chunks)
            try:
                from thedas_io.export import export_bytes, remember
                from thedas_io.geometry import flatten
                reg = remember("u.mmh", "mmh", tree, uraw)
                reg["hosts"] = stats["hosts"]
                same = export_bytes(reg) == uraw
                hook = sorted((o for o in bpy.context.scene.objects
                               if o.type == "EMPTY"), key=lambda o: o.name)[0]
                before = tuple(hook.matrix_world.translation)
                hook.matrix_world.translation = tuple(
                    Vector(before) + Vector((0.05, 0.0, 0.0)))
                moved_raw = export_bytes(reg)
                want = {n.name: loc for n, _, loc, _ in flatten(tree) if n.name}
                got = {n.name: loc for n, _, loc, _ in
                       flatten(MMHReader(moved_raw).tree) if n.name}
                moved = all(abs(a - b) < 1e-4 for a, b in zip(
                    got[hook.name], (before[0] + 0.05, before[1], before[2])))
                kept = all(got[k] == want[k] for k in want if k != hook.name)
                out = {"bones": stats["bones"], "empties": stats["empties"],
                       "same": same, "moved": moved and moved_raw != uraw,
                       "kept": kept}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    ) == {"bones": 68, "empties": 16, "same": True, "moved": True, "kept": True}


_SETUP = textwrap.dedent("""\
    import io
    import os
    import shutil
    import sys
    import tempfile
    def setup():
        sys.path.insert(0, "src")
        import thedas_io as ext
        from thedas_io.asset_db.scanner import scan
        from thedas_io.erf.description import ERFWriter
        from PIL import Image
        tmp = tempfile.mkdtemp()
        shutil.copy(os.path.join("data", "c_corspidr_3.msh"), os.path.join(tmp, "x.msh"))
        for name, color in (("x_0d.dds", (200, 100, 50, 255)),
                            ("x_0n.dds", (128, 128, 128, 128))):
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), color).save(buf, "DDS")
            open(os.path.join(tmp, name), "wb").write(buf.getvalue())
        open(os.path.join(tmp, "x.mao"), "w").write(
            '<MaterialObject Name="X"><Material Name="x.mat"></Material>'
            '<DefaultSemantic Name="Default"></DefaultSemantic>'
            '<Texture Name="a" ResName="x_0d.dds"></Texture>'
            '<Texture Name="b" ResName="x_0n.dds"></Texture>'
            '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')
        w = ERFWriter()
        w.add_entry("x.msh", open(os.path.join(tmp, "x.msh"), "rb").read())
        with open(os.path.join(tmp, "p.erf"), "wb") as f:
            f.write(w.write())
        db = os.path.join(tmp, "t.sqlite")
        scan(tmp, db)
        try:
            ext.unregister()
        except Exception:
            pass
        ext.register()
        import bpy
        sc = bpy.context.scene
        sc.thedas_io_db_path = db
        bpy.ops.dao.refresh_assets()
        for i, item in enumerate(sc.thedas_io_assets):
            if item.filename == "x.msh":
                sc.thedas_io_index = i
                bpy.ops.dao.import_asset()
                break  # loose + archive rows share a filename; import once
        return tmp

    def cleanup():
        import thedas_io as ext
        import bpy
        for o in list(bpy.context.scene.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)
        for name in ("x_0d.dds", "x_0n.dds"):
            img = bpy.data.images.get(name)
            if img is not None:
                bpy.data.images.remove(img)
        ext.unregister()
    """)


def test_import_material_diagnostics():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import thedas_io as ext
            import bpy
            try:
                notes = []
                mat = ext.ui.apply_materials(os.path.join(tmp, "missing.mao"), [],
                                             notes=notes)
                open(os.path.join(tmp, "bad.mao"), "w").write(
                    '<MaterialObject Name="B"><Material Name="b.mat"></Material>'
                    '<DefaultSemantic Name="Default"></DefaultSemantic>'
                    '<Texture Name="a" ResName="x_0d.dds"></Texture>'
                    '<Texture Name="b" ResName="nope.dds"></Texture>'
                    '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')
                notes2 = []
                mat2 = ext.ui.apply_materials(os.path.join(tmp, "bad.mao"), [],
                                              notes=notes2)
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(buf, "DDS")
                open(os.path.join(tmp, "x_0t.dds"), "wb").write(buf.getvalue())
                open(os.path.join(tmp, "tint.mao"), "w").write(
                    '<MaterialObject Name="T"><Material Name="t.mat"></Material>'
                    '<DefaultSemantic Name="ArmourSkinTint"></DefaultSemantic>'
                    '<Texture Name="mml_tDiffuse" ResName="x_0d.dds"></Texture>'
                    '<Texture Name="mml_tTintMask" ResName="x_0t.dds"></Texture>'
                    '</MaterialObject>')
                notes3 = []
                mat3 = ext.ui.apply_materials(os.path.join(tmp, "tint.mao"), [],
                                              notes=notes3)
                out = {"mat": mat, "notes": notes,
                       "mat2": mat2.name if mat2 else None, "notes2": notes2,
                       "mat3": mat3.name if mat3 else None, "notes3": notes3,
                       "props": list(mat2["v"]) if mat2 and "v" in mat2 else []}
                texes = [n for n in mat2.node_tree.nodes if n.type == "TEX_IMAGE"] \
                    if mat2 else []
                ph = next((n for n in texes if n.image is not None
                           and n.image.name == "nope.dds"), None)
                out["placeholder"] = None if ph is None else {
                    "tag": ph.image.get("thedas_io_missing"),
                    "px": list(ph.image.pixels[:4])}
            finally:
                cleanup()
            return out
        """)
    ) == {
        "mat": None,
        "notes": ["MAO not found: missing.mao (scan its package first)"],
        "mat2": "B",
        "notes2": ["bad.mao: textures not found: nope.dds"],
        "mat3": "T",
        "notes3": [
            (
                "tint.mao: tint mask present but no tint.tnt found; "
                "preview is untinted while the game tints "
                "via the item variation"
            )
        ],
        "props": [1, 2, 3, 4],
        "placeholder": {"tag": "nope.dds", "px": [1.0, 0.0, 1.0, 1.0]},
    }


def test_import_msh_with_material():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import bpy
            try:
                objs = [o for o in bpy.context.scene.objects if o.name == "C_CORSPIDR_Mesh1"]
                mat = objs[0].data.materials[0] if objs else None
                out = {"mat": mat.name if mat else None,
                       "links": sorted(l.to_socket.name for l in mat.node_tree.links) if mat else [],
                       "props": list(mat["v"]) if mat and "v" in mat else []}
            finally:
                cleanup()
            return out
        """)
    ) == {
        "mat": "X",
        "links": [
            "Base Color",
            "Base Color",
            "Color",
            "Normal",
            "Normal",
            "Surface",
            "Surface",
            "Vector",
            "Vector",
            "Vector",
        ],
        "props": [1.0, 2.0, 3.0, 4.0],
    }


def test_export_untouched_roundtrip():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import thedas_io.export as ex
            import bpy
            out_dir = os.path.join(tmp, "out")
            try:
                res = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                names = sorted(ex.ITEMS)
                same = {n: open(os.path.join(out_dir, n), "rb").read()
                        == open(os.path.join(tmp, n), "rb").read() for n in names}
            finally:
                cleanup()
            return {"res": res, "names": names, "same": same}
        """)
    ) == {
        "res": ["FINISHED"],
        "names": ["x.mao", "x.msh", "x_0d.dds", "x_0n.dds"],
        "same": {"x.mao": True, "x.msh": True, "x_0d.dds": True, "x_0n.dds": True},
    }


def test_tangent_recompute_matches_stored():
    """Gate: Blender MikkTSpace == game tangent convention on untouched data.

    Recomputes loop tangents on a fresh import and diffs against the stored
    tangent/binormal rows. Mean agreement must be tight (a convention error
    - flipped signs, swapped axes - lands near 1.0-2.0, not noise); maxima
    are not gated because degenerate-UV islands legitimately differ between
    tangent algorithms. Frames must be unit triplets on average with w
    uniformly 1.0. If the means ever fail, _patch_tangents must fall back
    to keep-source-bytes, not ship inverted shading.
    """
    out = run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            import math
            import os
            import sys
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.export import _loop_tangents
            from thedas_io.geometry import decode_chunk
            from thedas_io.msh.description import MSHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            raw = open(os.path.join("data", "c_corspidr_3.msh"), "rb").read()
            mesh = MSHReader(raw).mesh
            objs = [ext.ui.build_chunk_object(bpy.context, mesh, c, raw, "c_corspidr_3.msh")
                    for c in mesh.chunks]
            try:
                obj = objs[0]
                build = decode_chunk(mesh, mesh.chunks[0])
                row_loop = {}
                for i, poly in enumerate(obj.data.polygons):
                    for j, r in enumerate(build["triangles"][i]):
                        row_loop.setdefault(r, poly.loop_start + j)
                got = _loop_tangents(obj.data, row_loop)
                diffs, units, dots, ws = [], [], [], []
                for r, (t, b) in got.items():
                    wt = build["tangents"][6][r]
                    wb = build["tangents"][7][r]
                    diffs.extend([abs(h - w) for h, w in zip(t, wt[:3])])
                    diffs.extend([abs(h - w) for h, w in zip(b, wb[:3])])
                    ws.extend([wt[3], wb[3]])
                    n = build["normals"][r]
                    units.append(abs(math.dist(t, (0, 0, 0)) - 1.0))
                    units.append(abs(math.dist(b, (0, 0, 0)) - 1.0))
                    dots.append(abs(t[0] * n[0] + t[1] * n[1] + t[2] * n[2]))
                out = {"rows": len(got),
                       "mean": round(sum(diffs) / len(diffs), 4),
                       "unit_mean": round(sum(units) / len(units), 6),
                       "ortho_mean": round(sum(dots) / len(dots), 6),
                       "ortho_max": round(max(dots), 4),
                       "w": sorted(set(w for w in ws))}
            finally:
                for o in objs:
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """)
    )
    assert out["rows"] == 1100
    assert out["mean"] < 0.2  # convention failure reads ~1.0, not noise
    assert out["unit_mean"] < 0.01  # degenerate-UV rows excluded from max
    assert out["ortho_mean"] < 0.05
    assert out["w"] == [1.0]


def test_export_edited_items():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def _compare(src_path, out_path):
            from thedas_io.geometry import decode_chunk, weld_positions
            from thedas_io.msh.description import MSHReader
            a = MSHReader(open(src_path, "rb").read()).mesh
            b = MSHReader(open(out_path, "rb").read()).mesh
            da = decode_chunk(a, a.chunks[0])
            db = decode_chunk(b, b.chunks[0])

            def drift(pairs, tol=1e-6):
                return all(abs(x - y) <= tol for pa, pb in pairs for x, y in zip(pa, pb))

            # the moved Blender vert fans out to every twin row on export
            _, mp = weld_positions(da["positions"])
            moved_rows = [r for r in range(len(da["positions"])) if mp[r] == 0]
            pos = [(da["positions"][r], db["positions"][r])
                   for r in range(len(da["positions"])) if r not in moved_rows]
            twins = [(da["positions"][r], db["positions"][r]) for r in moved_rows]
            # tangent frames follow positions/normals: rows whose frame
            # drifted (the edit plus its tilted neighbors) get recomputed
            # values, the rest keep source bytes exactly
            reframed = {
                r
                for r in range(len(da["positions"]))
                if max(abs(a - b) for a, b in
                       zip(db["positions"][r], da["positions"][r])) > 1e-6
                or (any(da["normals"][r])
                    and max(abs(a - b) for a, b in
                            zip(db["normals"][r], da["normals"][r])) > 2e-3)
            }
            uv = [list(zip(da["uvs"][k], db["uvs"][k])) for k in sorted(da["uvs"])]

            def flat(groups):
                return {(b, v): w for b, members in groups.items() for v, w in members}

            skin_a, skin_b = flat(da["groups"]), flat(db["groups"])
            mx = [max(p[i] for p in db["positions"]) for i in range(3)]
            bounds = b.chunks[0].bounds
            return {"counts": (len(db["positions"]), len(db["triangles"]))
                    == (len(da["positions"]), len(da["triangles"])),
                    "moved": abs(db["positions"][0][0] - (da["positions"][0][0] + 0.05)) < 5e-3,
                    "twins": (len(moved_rows) > 1
                              and all(abs(pb[0] - (pa[0] + 0.05)) < 5e-3
                                      for pa, pb in twins)),
                    "others": drift(pos),
                    "tris": da["triangles"] == db["triangles"],
                    "uv": all(drift(layer) for layer in uv),
                    "tangent": (set(da["tangents"]) == set(db["tangents"])
                                == {6, 7} and all(
                                    da["tangents"][u][r] == db["tangents"][u][r]
                                    for u in (6, 7)
                                    for r in range(len(da["positions"]))
                                    if r not in reframed)),
                    "tangent_fresh": any(
                        da["tangents"][u][r] != db["tangents"][u][r]
                        for u in (6, 7) for r in reframed),
                    "groups": skin_a.keys() == skin_b.keys()
                    and all(abs(skin_a[k] - skin_b[k]) <= 1e-3 for k in skin_a),
                    "bounds": all(abs(bounds[8018][i] - mx[i]) <= 1e-6 for i in range(3))}

        def main():
            tmp = setup()
            import numpy as np
            import bpy
            from PIL import Image
            img = bpy.data.images["x_0d.dds"]
            px = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(px)
            px[:4] = (0.0, 1.0, 0.0, 1.0)
            img.pixels.foreach_set(px)
            obj = bpy.data.objects["C_CORSPIDR_Mesh1"]
            obj.data.vertices[0].co[0] += 0.05
            obj.data.update()
            out_dir = os.path.join(tmp, "out")
            try:
                res = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                same = {n: open(os.path.join(out_dir, n), "rb").read()
                        == open(os.path.join(tmp, n), "rb").read()
                        for n in ("x.mao", "x.msh", "x_0d.dds", "x_0n.dds")}
                dds = Image.open(os.path.join(out_dir, "x_0d.dds"))
                dds.load()
                geo = _compare(os.path.join(tmp, "x.msh"), os.path.join(out_dir, "x.msh"))
            finally:
                cleanup()
            return {"res": res, "same": same, "dds": [dds.format, list(dds.size)], "geo": geo}
        """)
    ) == {
        "res": ["FINISHED"],
        "same": {"x.mao": True, "x.msh": False, "x_0d.dds": False, "x_0n.dds": True},
        "dds": ["DDS", [8, 8]],
        "geo": {
            "counts": True,
            "moved": True,
            "twins": True,
            "others": True,
            "tris": True,
            "uv": True,
            "tangent": True,
            "tangent_fresh": True,
            "groups": True,
            "bounds": True,
        },
    }


def test_export_edited_mao():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import bpy
            from thedas_io.materials import parse_mao
            mat = bpy.data.materials["X"]
            mat["v"] = [9.0, 8.0, 7.0, 6.0]
            alt = bpy.data.images["x_0d.dds"].copy()
            alt.name = "alt_0d.dds"
            swapped = None
            for node in mat.node_tree.nodes:
                if (node.type == "TEX_IMAGE" and node.image is not None
                        and node.image.name == "x_0d.dds"):
                    node.image = alt
                    swapped = node.name
            out_dir = os.path.join(tmp, "out")
            try:
                res = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                out = parse_mao(open(os.path.join(out_dir, "x.mao"),
                                     encoding="utf-8").read())
                same_msh = open(os.path.join(out_dir, "x.msh"), "rb").read() == open(
                    os.path.join(tmp, "x.msh"), "rb").read()
                same_dds = open(os.path.join(out_dir, "x_0d.dds"), "rb").read() == open(
                    os.path.join(tmp, "x_0d.dds"), "rb").read()
            finally:
                cleanup()
            return {"res": res, "swapped": swapped is not None,
                    "vectors": out["vectors"], "textures": out["textures"],
                    "same_msh": same_msh, "same_dds": same_dds}
        """)
    ) == {
        "res": ["FINISHED"],
        "swapped": True,
        "vectors": {"v": [9.0, 8.0, 7.0, 6.0]},
        "textures": {"a": "alt_0d.dds", "b": "x_0n.dds"},
        "same_msh": True,
        "same_dds": True,
    }


def test_import_mmh_material_by_6001():
    assert run(
        textwrap.dedent("""\
        import os
        import shutil
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            tmp = tempfile.mkdtemp()
            shutil.copy(os.path.join("data", "c_corspidra_0.mmh"),
                        os.path.join(tmp, "y.mmh"))
            shutil.copy(os.path.join("data", "c_corspidr_0.msh"),
                        os.path.join(tmp, "c_corspidr_0.msh"))
            open(os.path.join(tmp, "c_corspidr.mao"), "w").write(
                '<MaterialObject Name="M6001"><Material Name="m.mat"></Material>'
                '<DefaultSemantic Name="Default"></DefaultSemantic>'
                '<Vector4f Name="v" value="1 2 3 4"></Vector4f></MaterialObject>')
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            res = None
            for i, item in enumerate(sc.thedas_io_assets):
                if item.filename == "y.mmh":
                    sc.thedas_io_index = i
                    res = sorted(bpy.ops.dao.import_asset())
            try:
                obj = bpy.data.objects["C_CORSPIDR_Mesh1"]
                mat = obj.data.materials[0] if obj.data.materials else None
                out = {"mat": mat.name if mat else None,
                       "props": list(mat["v"]) if mat and "v" in mat else []}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return {"res": res, "mat": out["mat"], "props": out["props"]}
        """)
    ) == {"res": ["FINISHED"], "mat": "M6001", "props": [1, 2, 3, 4]}


def test_reopening_restores_the_export_registry():
    """The reported bug: a reopened .blend says "Nothing imported yet".

    The registry is Python-side, so it is written into the scene as a JSON
    descriptor of names and re-resolved from the asset database on load.
    """
    first = run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            from types import SimpleNamespace
            from typing import Any, cast
            tmp, res = mmh_setup()
            try:
                import bpy
                from thedas_io.export import ITEMS
                from thedas_io.ui import _store_registry
                _store_registry(bpy.context.scene)
                bpy.ops.wm.save_as_mainfile(
                    filepath=os.path.join(tmp, "scene.blend"))
                return {"blend": os.path.join(tmp, "scene.blend"),
                        "db": bpy.context.scene.thedas_io_db_path,
                        "items": sorted(ITEMS)}
            finally:
                mmh_cleanup()
        """)
    )
    assert first["items"], "the import should have registered something"
    again = run(
        textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            ext.register()          # so the load_post handler is installed
            import bpy
            bpy.ops.wm.open_mainfile(filepath=r"%s")
            from thedas_io.export import ITEMS, export_all
            scene = bpy.context.scene
            out = os.path.join(os.path.dirname(bpy.data.filepath), "out")
            scene.thedas_io_export_dir = out
            from thedas_io.ui import _sync_export_items
            _sync_export_items(scene)
            written, errors = export_all(out, scene.thedas_io_export_tint,
                                        scene.thedas_io_tint_name)
            return {"db": scene.thedas_io_db_path,
                    "items": sorted(ITEMS),
                    "result": sorted(written),
                    "errors": errors}
        """)
        % first["blend"].replace("\\", "\\\\")
    )
    assert again["db"] == first["db"]
    assert again["items"] == first["items"]  # the registry came back
    assert again["errors"] == []  # and every resource re-encodes
    assert "c_corspidra_0.mmh" in again["result"]
    assert "c_corspidr_0.msh" in again["result"]


def test_external_edit_reload_cycle():
    """import -> save -> edit the file -> reload -> export sees the edit.

    The pixels are packed into the .blend so reopening works, and the file in
    thedas_io_textures/ is re-read on demand because a packed image ignores
    image.reload(). Export never reads the file: it compares stock bytes to
    these pixels, so the cache may be any format Pillow reads.
    """
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from types import SimpleNamespace
            from PIL import Image
            project = tempfile.mkdtemp()
            bpy.ops.wm.save_as_mainfile(
                filepath=os.path.join(project, "scene.blend"))
            src = os.path.join(project, "src")
            os.makedirs(src, exist_ok=True)
            path = os.path.join(src, "p_0d.dds")
            buf = io.BytesIO()
            Image.new("RGBA", (4, 4), (64, 64, 64, 255)).save(buf, "DDS")
            open(path, "wb").write(buf.getvalue())
            out = {}
            try:
                img = ext.ui.load_image(path)
                out["packed"] = img.packed_file is not None
                out["before"] = [round(v, 3) for v in list(img.pixels[:4])]

                # an external edit, saved as PNG - a different format on purpose
                cached = os.path.join(project, "thedas_io_textures", "p_0d.dds")
                buf2 = io.BytesIO()
                Image.new("RGBA", (4, 4), (200, 10, 10, 255)).save(buf2, "PNG")
                open(cached, "wb").write(buf2.getvalue())

                # nothing changes until the reload
                out["stale"] = [round(v, 3) for v in list(img.pixels[:4])]
                ext.ui.reload_textures()
                out["after"] = [round(v, 3) for v in list(img.pixels[:4])]
                out["still_packed"] = img.packed_file is not None

                # and the export must notice the difference
                from thedas_io.export import export_all
                out_dir = os.path.join(project, "out")
                _written, out["errors"] = export_all(out_dir)
                out["exported_changed"] = (
                    open(os.path.join(out_dir, "p_0d.dds"), "rb").read()
                    != open(path, "rb").read())
            finally:
                for im in list(bpy.data.images):
                    if im.name == "p_0d.dds":
                        bpy.data.images.remove(im)
                ext.unregister()
            return out
        """)
    ) == {
        "packed": True,
        "before": [0.251, 0.251, 0.251, 1.0],
        "stale": [0.251, 0.251, 0.251, 1.0],  # untouched until reload
        "after": [0.784, 0.039, 0.039, 1.0],  # PNG edit, read back
        "still_packed": True,
        "exported_changed": True,
        "errors": [],
    }


def test_reload_works_after_reopening_the_blend():
    """The reported bug: 'Reloaded 0 texture(s)' after reopening.

    Reopening restores the packed images but leaves the import registry
    empty, so reload must find textures from bpy.data.images instead.
    """
    first = run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            project = tempfile.mkdtemp()
            bpy.ops.wm.save_as_mainfile(
                filepath=os.path.join(project, "scene.blend"))
            src = os.path.join(project, "src")
            os.makedirs(src, exist_ok=True)
            path = os.path.join(src, "p_0d.dds")
            buf = io.BytesIO()
            Image.new("RGBA", (4, 4), (64, 64, 64, 255)).save(buf, "DDS")
            open(path, "wb").write(buf.getvalue())
            img = ext.ui.load_image(path)
            # the material needs a real user (a mesh) or Blender purges it,
            # and the packed image with it
            bpy.ops.mesh.primitive_cube_add()
            cube = bpy.context.active_object
            mat = bpy.data.materials.new("M")
            mat.use_nodes = True
            mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
            cube.data.materials.append(mat)
            bpy.ops.wm.save_as_mainfile(   # now save it for real
                filepath=os.path.join(project, "scene.blend"))
            return {"blend": os.path.join(project, "scene.blend"),
                    "cache": os.path.join(project, "thedas_io_textures")}
        """)
    )
    # a fresh Blender: packed images restored, ITEMS empty
    again = run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from PIL import Image
            bpy.ops.wm.open_mainfile(filepath=r"%s")
            import thedas_io.ui as ui
            from thedas_io.export import ITEMS
            img = bpy.data.images.get("p_0d.dds")
            registry = len([i for i in ITEMS.values() if i.get("kind") == "texture"])
            # an external edit, in PNG form again
            buf = io.BytesIO()
            Image.new("RGBA", (4, 4), (10, 200, 20, 255)).save(buf, "PNG")
            open(os.path.join(r"%s", "p_0d.dds"), "wb").write(buf.getvalue())
            done, missing, failed = ui.reload_textures()
            after = bpy.data.images.get("p_0d.dds")
            return {"registry": registry,
                    "reloaded": done,
                    "failed": failed,
                    "found": [n for _i, n in ui._texture_images()],
                    "after": ([round(v, 3) for v in list(after.pixels[:4])]
                              if after else None)}
        """)
        % (first["blend"].replace("\\", "\\\\"), first["cache"].replace("\\", "\\\\"))
    )
    assert again["registry"] == 0  # the registry really is empty
    assert again["found"] == ["p_0d.dds"]  # discovered from bpy.data.images
    assert again["reloaded"] == ["p_0d.dds"]  # reload still found it
    assert again["failed"] == []
    # 10,200,20 through a PNG round trip: exact in R and G, B off by one step
    assert again["after"] == [0.039, 0.784, 0.078, 1.0]


def test_reopening_the_blend_keeps_the_packed_texture():
    # The pixels live in the .blend, so no cache file is needed to reopen.
    first = run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            project = tempfile.mkdtemp()
            src = os.path.join(project, "src")
            os.makedirs(src, exist_ok=True)
            path = os.path.join(src, "p_0d.dds")
            buf = io.BytesIO()
            Image.new("RGBA", (4, 4), (64, 64, 64, 255)).save(buf, "DDS")
            open(path, "wb").write(buf.getvalue())
            img = ext.ui.load_image(path)
            mat = bpy.data.materials.new("M")
            mat.use_nodes = True
            mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
            # delete the cache: the .blend alone must carry the pixels
            import shutil
            shutil.rmtree(os.path.join(project, "thedas_io_textures"), ignore_errors=True)
            bpy.ops.wm.save_as_mainfile(
                filepath=os.path.join(project, "scene.blend"))
            return {"packed": img.packed_file is not None,
                    "blend": os.path.join(project, "scene.blend")}
        """)
    )
    assert first["packed"] is True
    again = run(
        textwrap.dedent("""\
        def main():
            import bpy
            bpy.ops.wm.open_mainfile(filepath=r"%s")
            img = bpy.data.images.get("p_0d.dds")
            if img is None:
                return {"found": False}
            return {"found": True,
                    "first": [round(v, 3) for v in list(img.pixels[:4])]}
        """)
        % first["blend"].replace("\\", "\\\\")
    )
    # the cache was deleted before saving, so these pixels came from the .blend
    assert again == {"found": True, "first": [0.251, 0.251, 0.251, 1.0]}


def test_export_edited_normal():
    assert run(
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import numpy as np
            import bpy
            from PIL import Image
            img = bpy.data.images["x_0n.dds"]
            px = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(px)
            px.reshape(-1, 4)[:] = (1.0, 127.0 / 255.0, 128.0 / 255.0, 1.0)
            img.pixels.foreach_set(px)
            out_dir = os.path.join(tmp, "out")
            try:
                res = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                same = {n: open(os.path.join(out_dir, n), "rb").read()
                        == open(os.path.join(tmp, n), "rb").read()
                        for n in ("x.mao", "x.msh", "x_0d.dds", "x_0n.dds")}
                got = Image.open(os.path.join(out_dir, "x_0n.dds"))
                got.load()
                rgba = got.convert("RGBA")
                w, h = rgba.size
                ch = [rgba.split()[i].getdata() for i in range(4)]
                import statistics as st
                stats = [[round(min(c)), round(max(c))] for c in ch]
            finally:
                cleanup()
            return {"res": res, "same": same, "size": [w, h], "stats": stats}
        """)
    ) == {
        "res": ["FINISHED"],
        "same": {"x.mao": True, "x.msh": True, "x_0d.dds": True, "x_0n.dds": False},
        "size": [8, 8],
        "stats": [[128, 128], [128, 128], [128, 128], [255, 255]],
    }


_MMH_SETUP = textwrap.dedent("""\
    import os
    import sys
    import tempfile
    def mmh_setup():
        sys.path.insert(0, "src")
        import thedas_io as ext
        from thedas_io.asset_db.scanner import scan
        from thedas_io.erf.description import ERFWriter
        tmp = tempfile.mkdtemp()
        for archive, entries in (("modelhierarchies.erf", ["c_corspidra_0.mmh"]),
                                 ("modelmeshdata.erf", ["c_corspidr_0.msh"])):
            w = ERFWriter()
            for name in entries:
                w.add_entry(name, open(os.path.join("data", name), "rb").read())
            open(os.path.join(tmp, archive), "wb").write(w.write())
        db = os.path.join(tmp, "t.sqlite")
        scan(tmp, db)
        try:
            ext.unregister()
        except Exception:
            pass
        ext.register()
        import bpy
        sc = bpy.context.scene
        sc.thedas_io_db_path = db
        bpy.ops.dao.refresh_assets()
        res = None
        for i, item in enumerate(sc.thedas_io_assets):
            if item.filename == "c_corspidra_0.mmh":
                sc.thedas_io_index = i
                res = sorted(bpy.ops.dao.import_asset())
        return tmp, res

    def mmh_cleanup():
        import thedas_io as ext
        import bpy
        for o in list(bpy.context.scene.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)
        ext.unregister()
    """)


def test_export_untouched_mmh():
    assert run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            out_dir = os.path.join(tmp, "out")
            try:
                from thedas_io.export import ITEMS
                exp = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                names = sorted(ITEMS)
                same = {n: open(os.path.join(out_dir, n), "rb").read()
                        == open(os.path.join("data", n), "rb").read() for n in names}
                stats = {"objects": len(bpy.context.scene.objects),
                         "bones": sum(len(o.data.bones) for o in bpy.context.scene.objects
                                      if o.type == "ARMATURE")}
            finally:
                mmh_cleanup()
            return {"res": res, "exp": exp, "names": names, "same": same, "stats": stats}
        """)
    ) == {
        "res": ["FINISHED"],
        "exp": ["FINISHED"],
        "names": ["c_corspidr_0.msh", "c_corspidra_0.mmh"],
        "same": {"c_corspidr_0.msh": True, "c_corspidra_0.mmh": True},
        "stats": {"objects": 18, "bones": 68},
    }


def test_export_edited_mmh():
    assert run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            from thedas_io.geometry import node_local
            from thedas_io.mmh.description import MMHReader
            arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
            bone = arm.data.edit_bones["Spine02"]
            head = bone.head
            bone.head = (head[0], head[1], head[2] + 0.05)
            bone.name = "Spine02X"
            bpy.ops.object.mode_set(mode="OBJECT")

            def locals_by_name(path):
                tree = MMHReader(open(path, "rb").read()).tree
                return {n.name: node_local(n.values) for n in tree.walk() if n.name}

            out_dir = os.path.join(tmp, "out")
            try:
                from thedas_io.export import ITEMS
                exp = sorted(bpy.ops.dao.export_assets(directory=out_dir))
                names = sorted(ITEMS)
                same = {n: open(os.path.join(out_dir, n), "rb").read()
                        == open(os.path.join("data", n), "rb").read() for n in names}
                src = locals_by_name(os.path.join("data", "c_corspidra_0.mmh"))
                out = locals_by_name(os.path.join(out_dir, "c_corspidra_0.mmh"))
                checks = {"renamed": "Spine02X" in out and "Spine02" not in out,
                          "others": set(src) - {"Spine02"} == set(out) - {"Spine02X"},
                          "moved": any(abs(a - b) > 1e-4 for a, b in zip(
                              src["Spine02"][0], out["Spine02X"][0])),
                          "untouched": all(src[k] == out[k] for k in src if k != "Spine02")}
            finally:
                mmh_cleanup()
            return {"res": res, "exp": exp, "names": names, "same": same, "checks": checks}
        """)
    ) == {
        "res": ["FINISHED"],
        "exp": ["FINISHED"],
        "names": ["c_corspidr_0.msh", "c_corspidra_0.mmh"],
        "same": {"c_corspidr_0.msh": True, "c_corspidra_0.mmh": False},
        "checks": {"renamed": True, "others": True, "moved": True, "untouched": True},
    }


def test_export_edited_msh_from_pose():
    # A posed bone must reach the MSH: the base mesh never moves on its own.
    assert run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            from thedas_io.export import export_bytes
            from thedas_io.export import ITEMS
            from thedas_io.geometry import decode_chunk
            from thedas_io.msh.description import MSHReader
            item = ITEMS["c_corspidr_0.msh"]

            def positions(raw):
                mesh = MSHReader(raw).mesh
                return decode_chunk(mesh, mesh.chunks[0])["positions"]

            try:
                src = positions(item["raw"])
                rest_clean = export_bytes(item) == item["raw"]
                arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
                pb = arm.pose.bones["Spine02"]
                pb.rotation_mode = "QUATERNION"
                pb.rotation_quaternion = (0.92387953, 0.38268343, 0.0, 0.0)
                bpy.context.view_layer.update()
                out_bytes = export_bytes(item)
                got = positions(out_bytes)
                moved = sum(1 for a, b in zip(src, got) if max(abs(x - y) for x, y in zip(a, b)) > 1e-4)
            finally:
                mmh_cleanup()
            return {"rest_clean": rest_clean, "changed": out_bytes != item["raw"],
                    "moved": moved > 0}
        """)
    ) == {"rest_clean": True, "changed": True, "moved": True}


def test_export_edited_msh_from_weight_paint():
    # Repainted weights are mesh data, so they must reach the MSH too.
    assert run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            from thedas_io.export import ITEMS, export_bytes
            from thedas_io.geometry import decode_chunk
            from thedas_io.msh.description import MSHReader
            item = ITEMS["c_corspidr_0.msh"]
            # the MSH has several chunks: keep the object and its chunk together
            src = MSHReader(item["raw"]).mesh
            idx, obj = next((i, o) for i, (o, _) in enumerate(item["objects"])
                            if o.get("thedas_io_bone_index"))
            chunk = src.chunks[idx]
            try:
                vg = obj.vertex_groups["Spine02"]
                local = obj["thedas_io_bone_index"]["Spine02"]
                verts = [v.index for v in obj.data.vertices
                         if any(g.group == vg.index and g.weight > 0.0 for g in v.groups)][:16]
                vg.add(verts, 1.0, "REPLACE")
                bpy.context.view_layer.update()
                out_bytes = export_bytes(item)
                out = MSHReader(out_bytes).mesh
                from thedas_io.geometry import weld_positions
                mp = weld_positions(
                    decode_chunk(src, chunk)["positions"])[1]
                # painting merged verts covers every twin row, not just the
                # rows whose index happens to match: verify in row space
                covered = [r for r in range(len(mp)) if mp[r] in set(verts)]
                painted = {}
                for bone, members in decode_chunk(out, out.chunks[idx])["groups"].items():
                    for v, w in members:
                        if v in covered:
                            painted.setdefault(v, {})[bone] = w
                # a full paint dominates, and the stored row must sum to 1 (the
                # reader divides by it, so an unnormalized row would inflate the pose)
                dominated = all(d[local] == max(d.values()) for d in painted.values())
                normalized = all(abs(sum(d.values()) - 1.0) < 0.005 for d in painted.values())
            finally:
                mmh_cleanup()
            return {"changed": out_bytes != item["raw"],
                    "all_painted": len(painted) == len(covered) >= len(verts),
                    "dominated": dominated, "normalized": normalized}
        """)
    ) == {"changed": True, "all_painted": True, "dominated": True, "normalized": True}


def test_export_skips_msh_while_in_edit_mode():
    # Edit-mode moves live in the cage: exporting there would write stale bytes.
    assert run(
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            from thedas_io.export import export_all
            out_dir = os.path.join(tmp, "out")
            try:
                mesh_obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
                bpy.context.view_layer.objects.active = mesh_obj
                mesh_obj.select_set(True)
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.transform.translate(value=(0, 0, 0.5))
                written, errors = export_all(out_dir)
            finally:
                bpy.ops.object.mode_set(mode="OBJECT")
                mmh_cleanup()
            return {"written": sorted(written), "errors": len(errors),
                    "mentions_edit_mode": any("Edit Mode" in e for e in errors)}
        """)
    ) == {"written": ["c_corspidra_0.mmh"], "errors": 1, "mentions_edit_mode": True}


def test_extension_filter_is_a_combo_box():
    # Real RNA, not fake_bpy: the Extension row must be an ENUM with (None)/MSH/MMH.
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from thedas_io.asset_db.scanner import scan
            from thedas_io.erf.description import ERFWriter
            prop = bpy.types.Scene.bl_rna.properties["thedas_io_filter_ext"]
            items = [(it.identifier, it.name) for it in prop.enum_items]
            kind, default = prop.type, prop.default
            tmp = tempfile.mkdtemp()
            w = ERFWriter()
            w.add_entry("a.msh", b"mesh")
            w.add_entry("b.mmh", b"tree")
            open(os.path.join(tmp, "p.erf"), "wb").write(w.write())
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            rows = {}
            for value in ("NONE", "MSH", "MMH"):
                sc.thedas_io_filter_ext = value
                bpy.ops.dao.refresh_assets()
                rows[value] = [r.filename for r in sc.thedas_io_assets]
            ext.unregister()
            return {"kind": kind, "items": items, "default": default, "rows": rows}
        """)
    ) == {
        "kind": "ENUM",
        "items": [["NONE", "(None)"], ["MSH", "MSH"], ["MMH", "MMH"]],
        "default": "NONE",
        "rows": {"NONE": ["a.msh", "b.mmh"], "MSH": ["a.msh"], "MMH": ["b.mmh"]},
    }


def test_export_edited_msh_from_edit_mode():
    # The user flow: import, move a vertex in Edit Mode, export â€” the msh must land.
    assert run(
        textwrap.dedent("""\
        import os
        import shutil
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.erf.description import ERFWriter
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            tmp = tempfile.mkdtemp()
            for name in ("c_corspidra_0.mmh", "c_corspidr_0.msh"):
                shutil.copy(os.path.join("data", name), os.path.join(tmp, name))
            w = ERFWriter()
            for name in ("c_corspidra_0.mmh", "c_corspidr_0.msh"):
                w.add_entry(name, open(os.path.join(tmp, name), "rb").read())
            open(os.path.join(tmp, "p.erf"), "wb").write(w.write())
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            i = next(k for k, r in enumerate(sc.thedas_io_assets) if r.filename == "c_corspidra_0.mmh")
            sc.thedas_io_index = i
            imported = sorted(bpy.ops.dao.import_asset())
            try:
                obj = next(o for o in bpy.context.scene.objects
                           if o.type == "MESH" and o.get("thedas_io_bone_index"))
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.transform.translate(value=(0, 0, 0.5))
                out = os.path.join(tmp, "out")
                res = sorted(bpy.ops.dao.export_assets(directory=out))
                names = sorted(os.listdir(out))
                from thedas_io.export import ITEMS
                edited = (open(os.path.join(out, "c_corspidr_0.msh"), "rb").read()
                          != open(os.path.join("data", "c_corspidr_0.msh"), "rb").read())
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return {"imported": imported, "res": res, "names": names, "edited": edited}
        """)
    ) == {
        "imported": ["FINISHED"],
        "res": ["FINISHED"],
        "names": ["c_corspidr_0.msh", "c_corspidra_0.mmh"],
        "edited": True,
    }


def test_tint_export_from_real_sockets():
    # The material is the source of truth: real sockets, real tnt, real 6340.
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import struct
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.export import ITEMS, export_all, remember
            from thedas_io.materials import parse_mao
            from thedas_io.mmh.description import MMHReader
            from thedas_io.msh.description import MSHReader
            from thedas_io.tnt import NAMES, alpha_input, parse
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()
            images = {}
            for semantic, name, color in (("d", "q_0d", (200, 100, 50, 255)),
                                          ("s", "q_0s", (40, 30, 20, 200)),
                                          ("t", "q_0t", (10, 20, 30, 255))):
                buf = io.BytesIO()
                Image.new("RGBA", (8, 8), color).save(buf, "DDS")
                path = os.path.join(tmp, f"{name}.dds")
                open(path, "wb").write(buf.getvalue())
                images[semantic] = ext.ui.load_image(path)
                mao_text = ('<MaterialObject Name="M"><Material Name="m.mat"></Material>'
                            '<DefaultSemantic Name="Default"></DefaultSemantic>'
                            '<Texture Name="d" ResName="q_0d.dds"></Texture>'
                            '<Texture Name="s" ResName="q_0s.dds"></Texture>'
                            '<Texture Name="t" ResName="q_0t.dds"></Texture>'
                            '</MaterialObject>')
                mao = parse_mao(mao_text)
            raw = open(os.path.join("data", "c_corspidra_0.mmh"), "rb").read()
            try:
                mat = ext.ui.build_material("M", mao, images)
                groups = {n.node_tree.name.split(" ", 2)[2]: n
                          for n in mat.node_tree.nodes if n.type == "GROUP"}
                # values a user would type into each group, incl. both split alphas
                typed = {"Diffuse": {"Diffuse R": (0.25, 0.5, 0.75, 0.9),
                                     "Diffuse opacity": (0.5, 0.4, 0.3, 0.9)},
                         "Specular": {"Specular B": (0.1, 0.2, 0.3, 0.5),
                                      "Specular opacity": (0.0, 0.0, 0.0, 0.0)}}
                for side, values in typed.items():
                    for name, colour in values.items():
                        groups[side].inputs[name].default_value = colour
                        if alpha_input(name):
                            groups[side].inputs[alpha_input(name)].default_value = colour[3]
                sraw = open(os.path.join("data", "c_corspidr_0.msh"), "rb").read()
                obj = ext.ui.build_chunk_object(
                    bpy.context, MSHReader(sraw).mesh, MSHReader(sraw).mesh.chunks[0],
                    sraw, "c_corspidr_0.msh")
                obj.data.materials.clear()
                obj.data.materials.append(mat)
                item = remember("c_corspidra_0.mmh", "mmh", MMHReader(raw).tree, raw)
                item["meshes"] = [obj]
                remember("m.mao", "mao", mao, mao_text.encode("utf-8"))
                out = os.path.join(tmp, "out")
                written, errors = export_all(out, tint=True)
                # the tint rides in the MAO as mml_vTintMaskColours: no .tnt file
                baked = parse_mao(open(os.path.join(out, "m.mao"), encoding="utf-8").read())
                array = [a for _, a in baked["extra"] if a.get("Name") == "mml_vTintMaskColours"]
                flat = [float(x) for x in array[0]["value"].split()]
                values = {n: tuple(flat[i * 4:i * 4 + 4]) for i, n in enumerate(NAMES)}
                wanted = {n: v for vals in typed.values() for n, v in vals.items()}
                got = {n: [round(v, 3) for v in values[n]] for n in wanted}
                untouched = {n: [round(v, 3) for v in values[n]]
                             for n in NAMES if n not in wanted}
                tnt_files = [f for f in os.listdir(out) if f.endswith(".tnt")]
                assert tnt_files == ["c_corspidra_0.tnt"]
                # the .tnt carries the same values the MAO array got
                from thedas_io.tnt import parse as parse_tnt
                disk = parse_tnt(open(os.path.join(out, "c_corspidra_0.tnt"), "rb").read())
                tnt_name = "c_corspidra_0.tnt"
                for n in wanted:
                    assert [round(v, 3) for v in disk[n]] == got[n]
                # the mmh on disk must now leave the variation tint off
                from thedas_io.gff40.description import GFFReader
                from thedas_io.export import _field, _gff_paths
                gff = GFFReader(open(os.path.join(out, "c_corspidra_0.mmh"), "rb").read())
                fourcc = int.from_bytes(b"mshh", "little")
                flags = [gff.decode_scalar(f, base)
                         for _, si, base in _gff_paths(gff, 0, 0)
                         if gff.struct_defs[si].type_fourcc == fourcc
                         for f in gff.struct_defs[si].field_instances
                         if f.label_id == 6340]
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                for m in list(bpy.data.materials):
                    bpy.data.materials.remove(m)
                for g in list(bpy.data.node_groups):
                    bpy.data.node_groups.remove(g)
                for name in ("q_0d.dds", "q_0s.dds", "q_0t.dds"):
                    img = bpy.data.images.get(name)
                    if img is not None:
                        bpy.data.images.remove(img)
                ITEMS.clear()
                ext.unregister()
            return {"written": sorted(written), "errors": errors, "got": got,
                    "untouched": untouched, "flags": flags, "tnt": tnt_name}
        """)
    ) == {
        "written": [
            "c_corspidr_0.msh",
            "c_corspidra_0.mmh",
            "c_corspidra_0.tnt",
            "m.mao",
            "q_0d.dds",
            "q_0s.dds",
            "q_0t.dds",
        ],
        "errors": [],
        "got": {
            "Diffuse R": [0.25, 0.5, 0.75, 0.9],
            "Specular B": [0.1, 0.2, 0.3, 0.5],
            "Diffuse opacity": [0.5, 0.4, 0.3, 0.9],
            "Specular opacity": [0.0, 0.0, 0.0, 0.0],
        },
        "untouched": {
            "Diffuse A": [1.0, 1.0, 1.0, 1.0],
            "Diffuse B": [1.0, 1.0, 1.0, 1.0],
            "Diffuse G": [1.0, 1.0, 1.0, 1.0],
            "Specular A": [1.0, 1.0, 1.0, 1.0],
            "Specular G": [1.0, 1.0, 1.0, 1.0],
            "Specular R": [1.0, 1.0, 1.0, 1.0],
        },
        "flags": [0],
        "tnt": "c_corspidra_0.tnt",
    }


def test_reimport_edited_msh(tmp_path):
    out_dir = str(tmp_path / "out").replace("\\", "/")
    pass1 = (
        _SETUP
        + textwrap.dedent("""\
        def main():
            tmp = setup()
            import bpy
            obj = bpy.data.objects["C_CORSPIDR_Mesh1"]
            obj.data.vertices[0].co[0] += 0.05
            obj.data.update()
            try:
                res = sorted(bpy.ops.dao.export_assets(directory="@OUT@"))
            finally:
                cleanup()
            return {"res": res}
        """)
    ).replace("@OUT@", out_dir)
    assert run(pass1) == {"res": ["FINISHED"]}
    pass2 = textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.geometry import decode_chunk
            from thedas_io.msh.description import MSHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            db = os.path.join("@OUT@", "t.sqlite")
            scan("@OUT@", db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            res = None
            for i, item in enumerate(sc.thedas_io_assets):
                if item.filename == "x.msh":
                    sc.thedas_io_index = i
                    res = sorted(bpy.ops.dao.import_asset())
            try:
                obj = bpy.data.objects["C_CORSPIDR_Mesh1"]
                got = [tuple(v.co) for v in obj.data.vertices]
                mesh = MSHReader(open(
                    os.path.join("data", "c_corspidr_3.msh"), "rb").read()).mesh
                want = decode_chunk(mesh, mesh.chunks[0])["positions"]
                # the stored weld map expands merged verts back to rows: the
                # moved Blender vert fans out to every twin row, the rest
                # must match the source exactly
                weld = list(obj["thedas_io_row_to_vert"])
                expanded = [got[v] for v in weld]
                moved = [r for r in range(len(want)) if weld[r] == 0]
                mat = obj.data.materials[0] if obj.data.materials else None
                out = {"res": res, "verts": len(got),
                       "mapped": len(weld),
                       "moved": abs(got[0][0] - (want[0][0] + 0.05)) < 5e-3,
                       "twins": (len(moved) > 1
                                 and all(abs(expanded[r][0] - (want[r][0] + 0.05)) < 5e-3
                                         for r in moved)),
                       "kept": all(abs(a - b) < 1e-6
                                   for r in range(len(want)) if r not in moved
                                   for a, b in zip(expanded[r], want[r])),
                       "mat": mat.name if mat else None}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """).replace("@OUT@", out_dir)
    assert run(pass2) == {
        "res": ["FINISHED"],
        "verts": 330,
        "mapped": 1100,
        "moved": True,
        "twins": True,
        "kept": True,
        "mat": "X",
    }


def test_reimport_edited_mmh(tmp_path):
    out_dir = str(tmp_path / "out").replace("\\", "/")
    pass1 = (
        _MMH_SETUP
        + textwrap.dedent("""\
        def main():
            tmp, res = mmh_setup()
            import bpy
            arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
            bone = arm.data.edit_bones["Spine02"]
            head = bone.head
            bone.head = (head[0], head[1], head[2] + 0.05)
            bone.name = "Spine02X"
            bpy.ops.object.mode_set(mode="OBJECT")
            try:
                exp = sorted(bpy.ops.dao.export_assets(directory="@OUT@"))
            finally:
                mmh_cleanup()
            return {"exp": exp}
        """)
    ).replace("@OUT@", out_dir)
    assert run(pass1) == {"exp": ["FINISHED"]}
    pass2 = textwrap.dedent("""\
        import os
        import sys
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.geometry import flatten
            from thedas_io.mmh.description import MMHReader
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            db = os.path.join("@OUT@", "t.sqlite")
            scan("@OUT@", db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            res = None
            for i, item in enumerate(sc.thedas_io_assets):
                if item.filename == "c_corspidra_0.mmh":
                    sc.thedas_io_index = i
                    res = sorted(bpy.ops.dao.import_asset())
            try:
                arm = next(o for o in bpy.context.scene.objects
                           if o.type == "ARMATURE")
                bones = arm.data.bones
                tree = MMHReader(open(os.path.join(
                    "data", "c_corspidra_0.mmh"), "rb").read()).tree
                want = {n.name: loc for n, _, loc, _ in flatten(tree) if n.name}
                # NB: Bone.head lies for parented bones in 5.2 (head reads
                # back permuted); matrix_local.translation is exact.
                head = tuple(bones["Spine02X"].matrix_local.translation)
                root = tuple(bones["Root"].matrix_local.translation)
                chunk = bpy.data.objects.get("C_CORSPIDR_Mesh1")
                out = {"res": res, "bones": len(bones),
                       "renamed": "Spine02X" in bones and "Spine02" not in bones,
                       "moved": all(abs(a - b) < 1e-4 for a, b in zip(
                           head, (want["Spine02"][0], want["Spine02"][1],
                                  want["Spine02"][2] + 0.05))),
                       "kept": all(abs(a - b) < 1e-5 for a, b in zip(
                           root, want["Root"])),
                       "chunk_parent": chunk.parent.name if chunk and chunk.parent else None}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return out
        """).replace("@OUT@", out_dir)
    assert run(pass2) == {
        "res": ["FINISHED"],
        "bones": 68,
        "renamed": True,
        "moved": True,
        "kept": True,
        "chunk_parent": "c_corspidra_0.mmh",
    }


def test_scan_button_populates_list():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.erf.description import ERFWriter
            tmp = tempfile.mkdtemp()
            w = ERFWriter()
            with open(os.path.join("data", "c_corspidr_3.msh"), "rb") as f:
                w.add_entry("a.msh", f.read())
            with open(os.path.join(tmp, "p.erf"), "wb") as f:
                f.write(w.write())
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            sc = bpy.context.scene
            sc.thedas_io_db_path = os.path.join(tmp, "t.sqlite")
            res = sorted(bpy.ops.dao.scan_assets(directory=tmp))
            names = [i.filename for i in sc.thedas_io_assets]
            subtypes = {p: bpy.types.Scene.bl_rna.properties[p].subtype
                        for p in ("thedas_io_export_dir",)}
            ext.unregister()
            return {"res": res, "names": names, "subtypes": subtypes}
        """)
    ) == {"res": ["FINISHED"], "names": ["a.msh"], "subtypes": {"thedas_io_export_dir": "DIR_PATH"}}


def test_autoload_saved_database():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.erf.description import ERFWriter
            tmp = tempfile.mkdtemp()
            w = ERFWriter()
            with open(os.path.join("data", "c_corspidr_3.msh"), "rb") as f:
                w.add_entry("a.msh", f.read())
            with open(os.path.join(tmp, "p.erf"), "wb") as f:
                f.write(w.write())
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            sc.thedas_io_assets.clear()
            ext.ui._on_file_load(None)
            names = [i.filename for i in sc.thedas_io_assets]
            ext.ui._on_file_load(None)
            twice = [i.filename for i in sc.thedas_io_assets]
            ext.unregister()
            return {"names": names, "twice": twice}
        """)
    ) == {"names": ["a.msh"], "twice": ["a.msh"]}


def test_import_archived_names_clean():
    assert run(
        textwrap.dedent("""\
        import io
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.erf.description import ERFWriter
            from thedas_io.export import ITEMS
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            from PIL import Image
            tmp = tempfile.mkdtemp()
            buf = io.BytesIO()
            Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(buf, "DDS")
            w = ERFWriter()
            with open(os.path.join("data", "c_corspidr_3.msh"), "rb") as f:
                w.add_entry("x.msh", f.read())
            w.add_entry("x.mao", b'<MaterialObject Name="X"><Material Name="x.mat"></Material>'
                                  b'<DefaultSemantic Name="Default"></DefaultSemantic>'
                                  b'<Texture Name="a" ResName="x_0d.dds"></Texture>'
                                  b'</MaterialObject>')
            w.add_entry("x_0d.dds", buf.getvalue())
            with open(os.path.join(tmp, "p.erf"), "wb") as f:
                f.write(w.write())
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            res = None
            for i, item in enumerate(sc.thedas_io_assets):
                if item.filename == "x.msh" and item.package == "p.erf":
                    sc.thedas_io_index = i
                    res = sorted(bpy.ops.dao.import_asset())
            try:
                obj = bpy.data.objects["C_CORSPIDR_Mesh1"]
                mat = obj.data.materials[0] if obj.data.materials else None
                out = {"items": sorted(ITEMS),
                       "images": sorted(i.name for i in bpy.data.images
                                        if i.name.endswith(".dds")),
                       "mat": mat.name if mat else None}
            finally:
                for o in list(bpy.context.scene.objects):
                    bpy.data.objects.remove(o, do_unlink=True)
                ext.unregister()
            return {"res": res, "out": out}
        """)
    ) == {
        "res": ["FINISHED"],
        "out": {"items": ["x.mao", "x.msh", "x_0d.dds"], "images": ["x_0d.dds"], "mat": "X"},
    }


def test_browser_refresh_and_import():
    assert run(
        textwrap.dedent("""\
        import os
        import sys
        import tempfile
        def main():
            sys.path.insert(0, "src")
            import thedas_io as ext
            from thedas_io.asset_db.scanner import scan
            from thedas_io.erf.description import ERFWriter
            tmp = tempfile.mkdtemp()
            w = ERFWriter()
            with open(os.path.join("data", "c_corspidr_3.msh"), "rb") as f:
                w.add_entry("a.msh", f.read())
            with open(os.path.join(tmp, "p.erf"), "wb") as f:
                f.write(w.write())
            db = os.path.join(tmp, "t.sqlite")
            scan(tmp, db)
            try:
                ext.unregister()
            except Exception:
                pass
            ext.register()
            import bpy
            sc = bpy.context.scene
            sc.thedas_io_db_path = db
            bpy.ops.dao.refresh_assets()
            n = len(sc.thedas_io_assets)
            sc.thedas_io_index = 0
            res = bpy.ops.dao.import_asset()
            names = [i.filename for i in sc.thedas_io_assets]
            ext.unregister()
            return {"n": n, "res": sorted(res), "names": names}
        """)
    ) == {"n": 1, "res": ["FINISHED"], "names": ["a.msh"]}
