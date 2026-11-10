"""Material node group templates (Commit 2), proven in real headless Blender.

Node trees are bpy semantics the hand fake cannot model, so every assertion
here spawns Blender. Marked `blender`, like the rest of test_blender_real.py.
"""

from __future__ import annotations

import textwrap

import pytest

from blender_runner import run

pytestmark = pytest.mark.blender


def test_node_groups_are_created_with_role_sockets():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            from thedas_io.blender_materials import create_material_node_groups
            trees = create_material_node_groups()
            out = {}
            for tree in trees:
                inputs = [s.name for s in tree.interface.items_tree
                          if getattr(s, "in_out", "") == "INPUT"]
                outputs = [s.name for s in tree.interface.items_tree
                           if getattr(s, "in_out", "") == "OUTPUT"]
                out[tree.name] = {"inputs": inputs, "outputs": outputs,
                                  "fake_user": tree.use_fake_user}
            # a second call must hand back the same trees, not duplicates
            again = create_material_node_groups()
            out["same"] = [a is b for a, b in zip(trees, again)]
            return out
        """)
    ) == {
        "ThedasIO_Terrain": {
            "inputs": [
                "palette",
                "normal",
                "height",
                "maskV",
                "maskA",
                "maskA2",
                "paletteParams",
                "paletteDimensions",
            ],
            "outputs": ["Base Color", "Normal", "Height"],
            "fake_user": True,
        },
        "ThedasIO_Water": {
            "inputs": [
                "normal",
                "normal2",
                "normal3",
                "waterDecal",
                "waterMask",
                "waterColor",
                "waveParams",
                "waveParams2",
                "waveParams3",
                "waterVisual",
                "normal2Factor",
                "normal3Factor",
            ],
            "outputs": ["Base Color", "Normal", "Alpha"],
            "fake_user": True,
        },
        "ThedasIO_Character": {
            "inputs": [
                "Base Color",
                "Normal",
                "ageDiffuse",
                "ageNormal",
                "tattoo",
                "browStubble",
                "browStubbleNormal",
                "browColour",
                "tattooColour",
                "AgeFactor",
                "AgeNormalFactor",
                "TattooFactor",
                "BrowFactor",
                "BrowNormalFactor",
            ],
            "outputs": ["Base Color", "Normal"],
            "fake_user": True,
        },
        "same": [True, True, True],
    }


def test_terrain_mao_import_wires_palette_and_seeds_params():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import build_material, get_material_node_group
            from thedas_io.materials import classify_material, parse_mao_bytes, slot_roles

            parsed = {
                "name": "kcc000d_79_0",
                "material": "terrain.mat",
                "semantic": "HighLod",
                "textures": {
                    "palette": "kcc000d__pal.dds",
                    "normal": "kcc000d__nrm.dds",
                    "mml_tReliefMapPalette": "kcc000d__hgt.dds",
                    "MaskV": "kcc000d_79_0_v.dds",
                    "MaskA": "kcc000d_79_0_a.dds",
                    "MaskA2": "kcc000d_79_0_a2.dds",
                    "mml_tLightmap": "LM.dds",
                    "LowLodMap": "kcc000d_79_lowlod.dds",
                },
                "vectors": {
                    "mml_vPalette_parameters": [0.0625, 0.03125, 0.375, 0.1875],
                    "mml_vPalette_dimensions": [0.5, 0.25, 2.0, 4.0],
                },
                "floats": {},
                "extra": [],
                "header": "",
            }
            images = {}
            for semantic in parsed["textures"]:
                img = bpy.data.images.new(semantic, 4, 4, alpha=True)
                images[semantic] = img
            kind = classify_material(parsed)
            mat = build_material("terrain_mat", parsed, images)
            group = get_material_node_group(kind)
            node = next(n for n in mat.node_tree.nodes
                        if n.type == "GROUP" and n.node_tree == group)
            wired = {s.name for s in node.inputs if s.is_linked}
            bsdf = next(n for n in mat.node_tree.nodes if n.type == "EEVEE_SPECULAR")
            return {
                "type": kind,
                "wired": sorted(wired),
                "params": [round(v, 6) for v in node.inputs["paletteParams"].default_value],
                "dims": [round(v, 6) for v in node.inputs["paletteDimensions"].default_value],
                "base_color_from_group": any(
                    l.from_node == node and l.to_socket.name == "Base Color"
                    for l in mat.node_tree.links),
                "texture_nodes": len([n for n in mat.node_tree.nodes
                                      if n.type == "TEX_IMAGE"]),
            }
        """)
    ) == {
        "type": "terrain",
        "wired": ["height", "maskA", "maskA2", "maskV", "normal", "palette"],
        "params": [0.0625, 0.03125, 0.375],
        "dims": [0.5, 0.25, 2.0],
        "base_color_from_group": True,
        # one node per distinct texture, reused by the group wiring
        "texture_nodes": 8,
    }


def test_water_mao_runs_its_normal_through_the_group():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import build_material, get_material_node_group
            from thedas_io.materials import classify_material

            extra = [
                ("Vector4fArray", {
                    "Name": "mat_vVSHWaterParams",
                    "value": "0.022 -9.61651e-010 0.1 0 "
                             "0.00939693 0.0034202 0.1 0 "
                             "0.00707107 0.00707107 0.1 0",
                }),
                ("Vector4fArray", {
                    "Name": "mat_vPSHWaterParams",
                    "value": "0.28 0.22 0.16 0 2.58 0.88 63 0.93",
                }),
            ]

            def probe(semantic, label):
                parsed = {
                    "name": "shl200d_water_119",
                    "material": "water.mat",
                    "semantic": "Default",
                    "textures": {semantic: "water_bump_n.dds"},
                    "vectors": {}, "floats": {}, "extra": extra, "header": "",
                }
                images = {semantic: bpy.data.images.new(label + "_n", 4, 4, alpha=True)}
                kind = classify_material(parsed)
                mat = build_material(label + "_mat", parsed, images)
                group = get_material_node_group(kind)
                node = next(n for n in mat.node_tree.nodes
                            if n.type == "GROUP" and n.node_tree == group)
                links = mat.node_tree.links
                drv = None
                if mat.node_tree.animation_data:
                    drv = next((d.driver.expression
                                for d in mat.node_tree.animation_data.drivers), None)
                bsdf = next(n for n in mat.node_tree.nodes if n.type == "EEVEE_SPECULAR")
                normal = [l for l in links
                          if l.to_node == bsdf and l.to_socket.name == "Normal"]
                layers = []
                for mapping in sorted((n for n in mat.node_tree.nodes
                                       if n.type == "MAPPING"), key=lambda n: n.name):
                    drift = next((l.from_node for l in mapping.inputs["Location"].links), None)
                    src = next((l.from_socket for l in mapping.inputs["Vector"].links), None)
                    layers.append({
                        "map": mapping.name,
                        "tile": [round(v, 3) for v in mapping.inputs["Scale"].default_value],
                        "drift": drift.name if drift else None,
                        "speed": ([round(drift.inputs[0].default_value[i], 6)
                                   for i in range(2)] if drift else None),
                        "coord": f"{src.node.type}.{src.name}" if src else None,
                    })
                gt = group
                weights = sorted(n.label for n in gt.nodes if n.label.startswith("wave weight"))
                return {
                    "type": kind,
                    "normal_wired": node.inputs["normal"].is_linked,
                    "normal2_wired": node.inputs["normal2"].is_linked,
                    "normal3_wired": node.inputs["normal3"].is_linked,
                    "factors": [node.inputs["normal2Factor"].default_value,
                                node.inputs["normal3Factor"].default_value],
                    "normal_from_group": len(normal) == 1 and normal[0].from_node == node,
                    "wave": [round(v, 6) for v in node.inputs["waveParams"].default_value],
                    "wave2": [round(v, 6) for v in node.inputs["waveParams2"].default_value],
                    "wave3": [round(v, 6) for v in node.inputs["waveParams3"].default_value],
                    "visual": [round(v, 6) for v in node.inputs["waterVisual"].default_value],
                    "layers": layers,
                    "weights": weights,
                    "renormalize": any(n.label == "renormalize ripples" for n in gt.nodes),
                    "clocks": len([n for n in mat.node_tree.nodes
                                   if n.name == "Water seconds"]),
                    "expression": drv,
                    "tex_nodes": len([n for n in mat.node_tree.nodes
                                      if n.type == "TEX_IMAGE"]),
                }

            return {"dao": probe("mat_tNormalMap", "dao"),
                    "da2": probe("mml_tWaterNormalMap", "da2")}
        """)
    ) == {
        "dao": {
            "type": "water",
            "normal_wired": True,
            "normal2_wired": True,
            "normal3_wired": True,
            "factors": [1.0, 1.0],
            "normal_from_group": True,
            "wave": [0.022, 0.0, 0.1],
            "wave2": [0.009397, 0.00342, 0.1],
            "wave3": [0.007071, 0.007071, 0.1],
            "visual": [0.28, 0.22, 0.16],
            "layers": [
                {
                    "map": "Water UV",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift",
                    "speed": [0.022, 0.0],
                    "coord": "TEX_COORD.Object",
                },
                {
                    "map": "Water UV 2",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift 2",
                    "speed": [0.009397, 0.00342],
                    "coord": "TEX_COORD.Object",
                },
                {
                    "map": "Water UV 3",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift 3",
                    "speed": [0.007071, 0.007071],
                    "coord": "TEX_COORD.Object",
                },
            ],
            "weights": ["wave weight 2", "wave weight 3"],
            "renormalize": True,
            "clocks": 1,
            "expression": "frame / 24",
            "tex_nodes": 3,
        },
        "da2": {
            "type": "water",
            "normal_wired": True,
            "normal2_wired": True,
            "normal3_wired": True,
            "factors": [1.0, 1.0],
            "normal_from_group": True,
            "wave": [0.022, 0.0, 0.1],
            "wave2": [0.009397, 0.00342, 0.1],
            "wave3": [0.007071, 0.007071, 0.1],
            "visual": [0.28, 0.22, 0.16],
            "layers": [
                {
                    "map": "Water UV",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift",
                    "speed": [0.022, 0.0],
                    "coord": "TEX_COORD.Object",
                },
                {
                    "map": "Water UV 2",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift 2",
                    "speed": [0.009397, 0.00342],
                    "coord": "TEX_COORD.Object",
                },
                {
                    "map": "Water UV 3",
                    "tile": [0.25, 0.25, 1.0],
                    "drift": "Water drift 3",
                    "speed": [0.007071, 0.007071],
                    "coord": "TEX_COORD.Object",
                },
            ],
            "weights": ["wave weight 2", "wave weight 3"],
            "renormalize": True,
            "clocks": 1,
            "expression": "frame / 24",
            "tex_nodes": 3,
        },
    }


def test_character_mao_uses_group_with_gated_factors():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import build_material, get_material_node_group
            from thedas_io.materials import classify_material

            parsed = {
                "name": "UH_HED_DEDa",
                "material": "Face.mat",
                "semantic": "Default",
                "textures": {
                    "mml_tDiffuse": "uh_hed_deda_0d.dds",
                    "mml_tNormalMap": "uh_hed_deda_0n.dds",
                    "mml_tTintMask": "uh_hed_maka_0t.dds",
                    "mml_tAgeDiffuseMap": "uh_hed_olda_0d.dds",
                    "mml_tAgeNormalMap": "uh_hed_olda_0n.dds",
                    "mml_tTattooMask": "uh_tat_p01_0t.dds",
                    "mml_tBrowStubble": "uh_hed_stb_0t.dds",
                    "mml_tBrowStubbleNormal": "uh_hed_stb_0n.dds",
                },
                "vectors": {},
                "floats": {},
                "extra": [],
                "header": "",
            }
            images = {}
            for semantic in parsed["textures"]:
                images[semantic] = bpy.data.images.new(semantic, 4, 4, alpha=True)
            kind = classify_material(parsed)
            mat = build_material("face_mat", parsed, images)
            group = get_material_node_group(kind)
            nodes = [n for n in mat.node_tree.nodes
                     if n.type == "GROUP" and n.node_tree == group]
            node = nodes[0]
            factors = {s.name: round(s.default_value, 3) for s in node.inputs
                       if s.name.endswith("Factor")}
            return {
                "type": kind,
                "group_nodes": len(nodes),
                "factors": factors,
                "age_diffuse_linked": node.inputs["ageDiffuse"].is_linked,
                "age_normal_linked": node.inputs["ageNormal"].is_linked,
                "wired": sorted(s.name for s in node.inputs if s.is_linked),
            }
        """)
    ) == {
        "type": "character",
        "group_nodes": 1,
        "factors": {
            "AgeFactor": 0.0,
            "AgeNormalFactor": 0.0,
            "TattooFactor": 0.0,
            "BrowFactor": 0.0,
            "BrowNormalFactor": 0.0,
        },
        "age_diffuse_linked": True,
        "age_normal_linked": True,
        "wired": [
            "Base Color",
            "Normal",
            "ageDiffuse",
            "ageNormal",
            "browStubble",
            "browStubbleNormal",
            "tattoo",
        ],
    }


def test_character_mao_without_face_maps_keeps_its_diffuse():
    # an eye MAO classifies as character but carries none of the age/tattoo/brow
    # maps: the empty group must not paint its default white over the diffuse
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import build_material
            from thedas_io.materials import classify_material

            parsed = {
                "name": "PN_EYE_NRMa",
                "material": "PN_EYE_NRMa",
                "semantic": "Eye",
                "textures": {
                    "mml_tDiffuse": "pn_eye_nrma_0d.dds",
                    "mml_tTintMask": "pn_eye_nrma_0t.dds",
                    "mml_tCorneaNormalMap": "pn_eye_nrmi_0n.dds",
                },
                "vectors": {},
                "floats": {},
                "extra": [],
                "header": "",
            }
            images = {
                semantic: bpy.data.images.new(semantic, 4, 4, alpha=True)
                for semantic in parsed["textures"]
            }
            kind = classify_material(parsed)
            mat = build_material("eye_mat", parsed, images)
            feeds = {}
            for node in mat.node_tree.nodes:
                if node.type not in ("EEVEE_SPECULAR", "BSDF_PRINCIPLED"):
                    continue
                for link in node.inputs["Base Color"].links:
                    feeds[node.type] = (
                        link.from_node.type,
                        getattr(getattr(link.from_node, "node_tree", None), "name", ""),
                    )
            return {"type": kind, "feeds": feeds}
        """)
    ) == {
        "type": "character",
        "feeds": {
            "EEVEE_SPECULAR": ["GROUP", "DAO Tint Diffuse"],
            "BSDF_PRINCIPLED": ["GROUP", "DAO Tint Diffuse"],
        },
    }


def test_lashes_alpha_drives_transparency_and_roughness_is_flat():
    # an eyelash map is a solid colour plus an alpha mask: the alpha has to reach
    # both outputs inverted, and a material with no spec map uses one roughness
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import DEFAULT_ROUGHNESS, build_material

            parsed = {
                "name": "PM_ULM_BAS",
                "material": "PM_ULM_BAS",
                "semantic": "Eyelash",
                "textures": {"mml_tDiffuse": "pm_ulm_bas_0d.dds"},
                "vectors": {},
                "floats": {},
                "extra": [],
                "header": "",
            }
            image = bpy.data.images.new("pm_ulm_bas_0d.dds", 4, 4, alpha=True)
            mat = build_material("lash_mat", parsed, {"mml_tDiffuse": image})
            out = {}
            for node in mat.node_tree.nodes:
                if node.type not in ("EEVEE_SPECULAR", "BSDF_PRINCIPLED"):
                    continue
                socket = node.inputs["Transparency" if node.type == "EEVEE_SPECULAR" else "Alpha"]
                source = socket.links[0].from_node if socket.is_linked else None
                out[node.type] = {
                    "inverted": source is not None and source.type == "INVERT",
                    "from_alpha": source is not None
                    and source.inputs["Color"].links[0].from_socket.name == "Alpha",
                    "roughness": None
                    if node.inputs["Roughness"].is_linked
                    else round(node.inputs["Roughness"].default_value, 3),
                }
            out["blend"] = mat.blend_method
            out["default"] = DEFAULT_ROUGHNESS
            return out
        """)
    ) == {
        "EEVEE_SPECULAR": {"inverted": True, "from_alpha": True, "roughness": 0.7},
        "BSDF_PRINCIPLED": {"inverted": True, "from_alpha": True, "roughness": 0.7},
        "blend": "BLEND",
        "default": 0.7,
    }


def test_standard_mao_keeps_no_group():
    assert run(
        textwrap.dedent("""\
        import sys
        def main():
            sys.path.insert(0, "src")
            import bpy
            from thedas_io.blender_materials import build_material, get_material_node_group
            from thedas_io.materials import classify_material

            parsed = {
                "name": "pn_arm_flta",
                "material": "Character.mat",
                "semantic": "Default",
                "textures": {"mml_tDiffuse": "x_0d.dds", "mml_tNormalMap": "x_0n.dds"},
                "vectors": {}, "floats": {}, "extra": [], "header": "",
            }
            images = {s: bpy.data.images.new(s, 4, 4, alpha=True)
                      for s in parsed["textures"]}
            kind = classify_material(parsed)
            mat = build_material("arm_mat", parsed, images)
            left = [n.name for n in mat.node_tree.nodes if n.type == "GROUP"
                    and get_material_node_group(kind) is not None
                    and n.node_tree == get_material_node_group(kind)]
            return {"type": kind, "group_nodes": len(left)}
        """)
    ) == {"type": "standard", "group_nodes": 0}
