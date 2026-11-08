"""Build Blender materials from DAO MAOs: channel packing, tints, transparency."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
import numpy as np

from .datablocks import tag
from .export import _LOADED_ITEMS, remember
from .materials import classify_material, parse_mao_bytes, slot_roles
from .textures import _existing_tint, _missing_image, _resolve_texture
from .tnt import DEFAULTS, alpha_input, for_side, group_name


def apply_materials(
    mao_path: Any,
    objs: list,
    color: Any = None,
    row: dict | None = None,
    db_path: Any = None,
    notes: list | None = None,
    local_only: bool = False,
    game: str | None = None,
) -> Any:
    # parsed mao onto chunk objects, silent when absent

    if mao_path is None:
        return None

    mao_path = Path(mao_path)
    if not mao_path.is_file():
        # mao in archive, pull raw bytes from loaded db items

        mao_bytes = None
        want = mao_path.name.lower()
        for item in _LOADED_ITEMS:
            if item["file"].lower() == want:
                mao_bytes = item["raw"]
                break
        if mao_bytes is None:
            if notes is not None:
                notes.append(f"MAO not found: {mao_path.name} (scan its package first)")
            return None
        parsed = parse_mao_bytes(mao_bytes)

        item = remember(mao_path.name, "mao", parsed, mao_bytes)
    else:
        mao_bytes = mao_path.read_bytes()
        parsed = parse_mao_bytes(mao_bytes)
        item = remember(mao_path.name, "mao", parsed, mao_bytes)

    images = {}
    for semantic, res in (parsed.get("textures") or {}).items():
        if not res:
            continue
        image = _resolve_texture(res, mao_path, row, db_path, local_only, game)
        if image is not None:
            images[semantic] = image
    missing = [
        res
        for semantic, res in (parsed.get("textures") or {}).items()
        if res and semantic not in images
    ]
    if missing and notes is not None:
        notes.append(f"{mao_path.name}: textures not found: {', '.join(missing)}")

    for semantic, res in (parsed.get("textures") or {}).items():
        if res and semantic not in images:
            images[semantic] = _missing_image(res, images)

    tint = _existing_tint(mao_path.stem, row, db_path, local_only, game)

    # classify material type
    material_type = classify_material(parsed)
    
    mat = build_material(parsed.get("name") or mao_path.stem, parsed, images, color, tint)
    
    # apply specialized node group if available
    node_group = get_material_node_group(material_type)
    if node_group is not None:
        _apply_node_group(mat, node_group, parsed, images)
    
    # diagnostic: log material transparency settings
    semantic = (parsed.get("semantic") or "").lower()
    is_transparent = semantic.startswith(("alpha", "punchthrough", "addvpunchthrough"))
    is_cutout = semantic.startswith(("punchthrough", "addvpunchthrough"))
    print(f"  Material '{mat.name}': semantic='{semantic}', type='{material_type}', "
          f"transparent={is_transparent}, cutout={is_cutout}, "
          f"blend_method={getattr(mat, 'blend_method', 'N/A')}, "
          f"alpha_threshold={getattr(mat, 'alpha_threshold', 'N/A')}")
    if is_transparent:
        print(f"  -> BLEND mode enabled (see-through)")
    elif is_cutout:
        print(f"  -> CLIP mode enabled (alpha test)")
    
    item["objects"].append(mat)  # live material for mao edit-back
    for obj in objs:
        obj.data.materials.append(mat)
    return mat


def _apply_node_group(mat: Any, node_group: Any, parsed: dict, images: dict) -> None:
    # apply a pre-built node group to the material
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    # find the group node or create one
    group_node = None
    for node in nodes:
        if node.type == "GROUP" and node.node_tree == node_group:
            group_node = node
            break
    
    if group_node is None:
        group_node = nodes.new("ShaderNodeGroup")
        group_node.node_tree = node_group
        group_node.location = (500.0, 0.0)
    
    # connect texture inputs to group
    roles = slot_roles(parsed.get("textures"))
    for semantic, image in images.items():
        role = roles.get(semantic)
        if role is None:
            continue
        
        tex_node = None
        for node in nodes:
            if node.type == "TEX_IMAGE" and node.image == image:
                tex_node = node
                break
        
        if tex_node is None:
            tex_node = _image_node(mat, image)
        
        # try to connect to group input
        if role in group_node.inputs:
            links.new(tex_node.outputs["Color"], group_node.inputs[role])
        elif role + " (RGB)" in group_node.inputs:
            links.new(tex_node.outputs["Color"], group_node.inputs[role + " (RGB)"])
            if _has_alpha(image) and (role + " (A)") in group_node.inputs:
                links.new(tex_node.outputs["Alpha"], group_node.inputs[role + " (A)"])
    
    # connect group outputs to BSDF
    out = nodes.get("Material Output")
    bsdf = None
    for node in nodes:
        if node.type in ("BSDF_PRINCIPLED", "BSDF_SPECULAR"):
            bsdf = node
            break
    
    if bsdf is not None and out is not None:
        if "Base Color" in group_node.outputs:
            links.new(group_node.outputs["Base Color"], bsdf.inputs["Base Color"])
        if "Normal" in group_node.outputs:
            links.new(group_node.outputs["Normal"], bsdf.inputs["Normal"])
        if "Height" in group_node.outputs:
            links.new(group_node.outputs["Height"], bsdf.inputs["Displacement"])
        if "Alpha" in group_node.outputs:
            links.new(group_node.outputs["Alpha"], bsdf.inputs["Alpha"])
        if "Emission" in group_node.outputs:
            links.new(group_node.outputs["Emission"], bsdf.inputs["Emission Color"])


def _seed_tint(group: Any, name: str, colour: Any) -> None:
    # push one tnt value into group, colour plus alpha float

    if name not in group.inputs:
        return
    alpha = group.inputs.get(alpha_input(name) or "")
    if alpha is None:
        group.inputs[name].default_value = tuple(colour)
        return
    group.inputs[name].default_value = tuple(colour)[:3] + (1.0,)  # mewo im a cat :33
    alpha.default_value = float(colour[3])


def build_material(name: str, mao: dict, images: dict, color: Any = None, tint: Any = None) -> Any:
    # specular-bsdf wiring from dao channel packing
    mat: Any = bpy.data.materials.new(name)
    tag(mat, "material")
    mat.use_nodes = True
    _wire_material(mat, mao, images, color=color, tint=tint)

    for key, values in (mao.get("vectors") or {}).items():
        mat[key] = list(values)
    return mat


# fixed slots keep built materials arranged the same way
_POS_UV = (-220.6, 72.9)
_POS_OUT = (1301.0, 46.2)
_POS_BSDF = (1111.0, 102.7)
_POS_TEX = {
    "diffuse": (352.6, 319.2),
    "normal": (352.6, -202.8),
    "specular": (352.6, 58.2),
    "tint": (352.6, 580.2),
    "emission": (352.6, -460.0),
    "packed": (352.6, 440.0),
}
_POS_PACKED_SEP = (180.0, 440.0)
_POS_NMAP = (788.1, -158.8)
_POS_GLOSS_INV = (796.4, -10.2)
_POS_SPEC_GAMMA = (950.0, 250.0)
_POS_PRINCIPLED = (1111.0, -620.0)
_POS_OUT_CYCLES = (1301.0, -620.0)
_POS_TRANS_INV = (796.4, -300.0)
_POS_GROUP = {"Diffuse": (769.6, 333.7), "Specular": (769.6, 733.7)}
_POS_MIX6335 = (975.0, -160.0)


def _wire_material(mat: Any, mao: dict, images: dict, color: Any = None, tint: Any = None) -> None:
    by_role: dict = {}
    packed: dict = {}
    roles = slot_roles(mao.get("textures"))
    for semantic, image in images.items():
        role = roles.get(semantic)
        if role == "diffuse" and "pack" in str(semantic).lower():
            packed[semantic] = image
        else:
            by_role.setdefault(role, image)

    nodes = mat.node_tree.nodes
    if "Principled BSDF" in nodes:
        nodes.remove(nodes["Principled BSDF"])
    out = nodes.get("Material Output", None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
    out.location = _POS_OUT
    out.target = "EEVEE"
    bsdf = nodes.new("ShaderNodeEeveeSpecular")
    bsdf.location = _POS_BSDF
    links = mat.node_tree.links
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    # no mapping node: the image is stored the right way up (see _blit), so the
    # raw UV renders correctly *and* texture paint lands where the preview shows
    # it. a scale of (1,-1,1) here un-flipped the render but paint ignores it.
    uvmap = nodes.new("ShaderNodeTexCoord")
    uvmap.location = _POS_UV
    vector = uvmap.outputs["UV"]
    base_feed = None
    normal_feed = None
    rough_feed = None
    trans_feed = None
    emission_feed = None
    diffuse = by_role.get("diffuse")
    spec = by_role.get("specular")
    if diffuse is not None:
        tex_diffuse = _image_node(mat, diffuse, vector, _POS_TEX["diffuse"])
        base_feed = tex_diffuse.outputs["Color"]
        if _has_alpha(diffuse):
            if _transparent_semantic(mao):
                trans_feed = _to_transparency(
                    mat, bsdf, tex_diffuse.outputs["Alpha"], cutout=_cutout_semantic(mao)
                )
            elif spec is None:
                # face-style map, alpha is the specular map
                links.new(tex_diffuse.outputs["Alpha"], bsdf.inputs["Specular"])
            # opaque with spec map, alpha is not opacity
    for image in packed.values():
        tex = _image_node(mat, image, vector, _POS_TEX["packed"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = _POS_PACKED_SEP
        links.new(tex.outputs["Color"], sep.inputs["Color"])
        base_feed = tex.outputs["Alpha"]
        if spec is None:
            links.new(sep.outputs["Green"], bsdf.inputs["Specular"])
        if _channel_varies(image, 2):
            trans_feed = _to_transparency(mat, bsdf, sep.outputs["Blue"])

    # character-specific texture slots
    age_diffuse = by_role.get("ageDiffuse")
    age_normal = by_role.get("ageNormal")
    tattoo = by_role.get("tattoo")
    brow_stubble = by_role.get("browStubble")
    brow_stubble_normal = by_role.get("browStubbleNormal")
    
    # special-purpose slots (stored as custom props for export)
    cornea_normal = by_role.get("corneaNormal")
    emotions_mask = by_role.get("emotionsMask")
    emotions_normal = by_role.get("emotionsNormal")
    relief_map = by_role.get("reliefMap")
    mask_v = by_role.get("maskV")
    mask_a = by_role.get("maskA")
    mask_a2 = by_role.get("maskA2")
    
    # terrain-specific slots
    palette = by_role.get("palette")
    palette_normal = by_role.get("paletteNormal")
    
    # water-specific slots
    water_normal = by_role.get("waterNormal")
    water_decal = by_role.get("waterDecal")
    water_mask = by_role.get("waterMask")

    tint_image = by_role.get("tint")
    tint_groups = {}
    if tint_image is not None and base_feed is not None:
        tex = _image_node(mat, tint_image, vector, _POS_TEX["tint"])
        for side in ("Diffuse", "Specular"):
            group = nodes.new("ShaderNodeGroup")
            group.node_tree = _tint_group(side)
            group.location = _POS_GROUP[side]
            links.new(tex.outputs["Color"], group.inputs["Tint mask (RGB)"])
            links.new(tex.outputs["Alpha"], group.inputs["Tint mask (A)"])
            for key, value in (tint or {}).items():
                _seed_tint(group, key, value)
            tint_groups[side] = group
        links.new(base_feed, tint_groups["Diffuse"].inputs["Diffuse"])
        base_feed = tint_groups["Diffuse"].outputs["Result"]

    if base_feed is not None:
        if color is not None and all(math.isfinite(c) for c in color):
            mix = nodes.new("ShaderNodeMix")
            mix.location = _POS_MIX6335
            mix.blend_type = "MULTIPLY"
            mix.data_type = "RGBA"
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs["B"].default_value = (*color[:3], 1.0)
            links.new(base_feed, mix.inputs["A"])
            base_feed = mix.outputs["Result"]
        links.new(base_feed, bsdf.inputs["Base Color"])

    normal = by_role.get("normal")
    if normal is not None:
        tex = _image_node(mat, normal, vector, _POS_TEX["normal"])
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = _POS_NMAP
        nmap.width = 160.0
        links.new(tex.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        normal_feed = nmap.outputs["Normal"]

    # character age maps (overlay on base)
    if age_diffuse is not None:
        tex = _image_node(mat, age_diffuse, vector, (352.6, 200.0))
        mix = nodes.new("ShaderNodeMix")
        mix.location = (700.0, 200.0)
        mix.blend_type = "MULTIPLY"
        mix.data_type = "RGBA"
        mix.inputs["Factor"].default_value = 1.0
        links.new(base_feed, mix.inputs["A"])
        links.new(tex.outputs["Color"], mix.inputs["B"])
        base_feed = mix.outputs["Result"]
    
    if age_normal is not None:
        tex = _image_node(mat, age_normal, vector, (352.6, -300.0))
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (788.1, -258.8)
        nmap.width = 160.0
        links.new(tex.outputs["Color"], nmap.inputs["Color"])
        # combine with main normal
        if normal_feed is not None:
            mix = nodes.new("ShaderNodeMix")
            mix.location = (1000.0, -200.0)
            mix.blend_type = "MIX"
            mix.data_type = "VECTOR"
            mix.inputs["Factor"].default_value = 0.5
            links.new(normal_feed, mix.inputs["A"])
            links.new(nmap.outputs["Normal"], mix.inputs["B"])
            normal_feed = mix.outputs["Result"]
        else:
            normal_feed = nmap.outputs["Normal"]

    # tattoo overlay
    if tattoo is not None:
        tex = _image_node(mat, tattoo, vector, (352.6, 150.0))
        mix = nodes.new("ShaderNodeMix")
        mix.location = (700.0, 150.0)
        mix.blend_type = "OVERLAY"
        mix.data_type = "RGBA"
        mix.inputs["Factor"].default_value = 1.0
        links.new(base_feed, mix.inputs["A"])
        links.new(tex.outputs["Color"], mix.inputs["B"])
        base_feed = mix.outputs["Result"]

    # brow stubble
    if brow_stubble is not None:
        tex = _image_node(mat, brow_stubble, vector, (352.6, 100.0))
        mix = nodes.new("ShaderNodeMix")
        mix.location = (700.0, 100.0)
        mix.blend_type = "MULTIPLY"
        mix.data_type = "RGBA"
        mix.inputs["Factor"].default_value = 1.0
        links.new(base_feed, mix.inputs["A"])
        links.new(tex.outputs["Color"], mix.inputs["B"])
        base_feed = mix.outputs["Result"]
    
    if brow_stubble_normal is not None:
        tex = _image_node(mat, brow_stubble_normal, vector, (352.6, -350.0))
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (788.1, -308.8)
        nmap.width = 160.0
        links.new(tex.outputs["Color"], nmap.inputs["Color"])
        if normal_feed is not None:
            mix = nodes.new("ShaderNodeMix")
            mix.location = (1000.0, -250.0)
            mix.blend_type = "MIX"
            mix.data_type = "VECTOR"
            mix.inputs["Factor"].default_value = 0.5
            links.new(normal_feed, mix.inputs["A"])
            links.new(nmap.outputs["Normal"], mix.inputs["B"])
            normal_feed = mix.outputs["Result"]
        else:
            normal_feed = nmap.outputs["Normal"]

    if spec is not None:
        tex = _image_node(mat, spec, vector, _POS_TEX["specular"])
        tint_specular = tint_groups.get("Specular")
        if tint_specular is not None:
            # tint the highlight per mask channel, same as the base colour
            links.new(tex.outputs["Color"], tint_specular.inputs["Specular"])
            spec_feed = tint_specular.outputs["Result"]
        else:
            spec_feed = tex.outputs["Color"]
        # preview-only calibration, export reads image names never links
        gamma = nodes.new("ShaderNodeGamma")
        gamma.location = _POS_SPEC_GAMMA
        gamma.inputs["Gamma"].default_value = 2.5
        links.new(spec_feed, gamma.inputs["Color"])
        links.new(gamma.outputs["Color"], bsdf.inputs["Specular"])
        invert = nodes.new("ShaderNodeInvert")
        invert.location = _POS_GLOSS_INV
        invert.inputs["Fac"].default_value = 0.5
        links.new(tex.outputs["Alpha"], invert.inputs["Color"])
        links.new(invert.outputs["Color"], bsdf.inputs["Roughness"])
        rough_feed = invert.outputs["Color"]

    emission = by_role.get("emission")
    if emission is not None:
        tex = _image_node(mat, emission, vector, _POS_TEX["emission"])
        links.new(tex.outputs["Color"], bsdf.inputs["Emissive Color"])
        emission_feed = tex.outputs["Color"]

    # cycles output from same maps, export ignores links
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = _POS_PRINCIPLED
    out_cycles = nodes.new("ShaderNodeOutputMaterial")
    out_cycles.location = _POS_OUT_CYCLES
    out_cycles.target = "CYCLES"
    if base_feed is not None:
        links.new(base_feed, principled.inputs["Base Color"])
    if rough_feed is not None:
        links.new(rough_feed, principled.inputs["Roughness"])
    if normal_feed is not None:
        links.new(normal_feed, principled.inputs["Normal"])
    if trans_feed is not None:
        links.new(trans_feed, principled.inputs["Alpha"])
    if emission_feed is not None:
        links.new(emission_feed, principled.inputs["Emission Color"])
    links.new(principled.outputs["BSDF"], out_cycles.inputs["Surface"])
    wired = {id(n.image) for n in nodes if n.type == "TEX_IMAGE" and n.image is not None}
    for semantic, image in list(images.items()):
        # unlinked nodes so editor shows unrecognized slots
        if id(image) not in wired:
            _image_node(mat, image)
            wired.add(id(image))
    by_semantic: dict = {}
    for semantic, image in list(images.items()):
        by_semantic.setdefault(id(image), semantic)
    for node in nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            node.label = by_semantic.get(id(node.image), "")
    
    # store special slots as custom properties on material for export
    special_slots = {
        "corneaNormal": cornea_normal,
        "emotionsMask": emotions_mask,
        "emotionsNormal": emotions_normal,
        "reliefMap": relief_map,
        "maskV": mask_v,
        "maskA": mask_a,
        "maskA2": mask_a2,
        "palette": palette,
        "paletteNormal": palette_normal,
        "waterNormal": water_normal,
        "waterDecal": water_decal,
        "waterMask": water_mask,
    }
    for key, image in special_slots.items():
        if image is not None:
            mat[f"thedas_io_{key}"] = image.name


def _tint_group(side: str) -> Any:
    # One shared group per side, built once. Creating one per material made
    # Blender uniquify the name ("DAO Tint Diffuse.001") for every material
    # after the first, and the exporter finds the group by exact name, so those
    # materials exported with no tint at all.
    name = group_name(side)
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree

    names = for_side(side)
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    tag(tree, "node_group")
    for name in ("Tint mask (RGB)", side, *names):
        sock = tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketColor")
        sock.default_value = DEFAULTS.get(name, (0.0, 0.0, 0.0, 1.0))
    alpha_names = [a for n in names if (a := alpha_input(n)) is not None]
    for name in ("Tint mask (A)", *alpha_names):
        tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket("Result", in_out="OUTPUT", socket_type="NodeSocketColor")
    nodes = tree.nodes
    links = tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-1180.0, -25.6)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (500.0, -500.0)
    mask = nodes.new("ShaderNodeSeparateColor")
    mask.location = (-800.0, -620.0)
    mask.label = "mask channels"
    links.new(gin.outputs["Tint mask (RGB)"], mask.inputs["Color"])
    masks = (
        mask.outputs["Red"],
        mask.outputs["Green"],
        mask.outputs["Blue"],
        gin.outputs["Tint mask (A)"],
    )

    opacity = nodes.new("ShaderNodeSeparateColor")
    opacity.location = (-520.0, -60.0)
    opacity.label = f"{side} opacity"
    links.new(gin.outputs[f"{side} opacity"], opacity.inputs["Color"])
    levels_in = (
        opacity.outputs["Red"],
        opacity.outputs["Green"],
        opacity.outputs["Blue"],
        gin.outputs[f"{side} opacity A"],
    )
    feed = gin.outputs[side]
    out = feed

    # channel, x, y, mask
    for (channel, x, y, _), mask_out, level_in in zip(
        (
            ("R", 300.0, 140.0, "Red"),
            ("G", 100.0, -40.0, "Green"),
            ("B", -100.0, -220.0, "Blue"),
            ("A", -300.0, -400.0, None),
        ),
        masks,
        levels_in,
    ):
        level = nodes.new("ShaderNodeMath")
        level.operation = "MULTIPLY"
        level.label = f"{side} {channel}"
        level.location = (x - 240.0, y)
        links.new(mask_out, level.inputs[0])
        links.new(level_in, level.inputs[1])
        mix = nodes.new("ShaderNodeMix")
        mix.blend_type = "MIX"
        mix.data_type = "RGBA"
        mix.label = f"{side} {channel} tint"
        mix.location = (x, y)
        links.new(out, mix.inputs["A"])
        links.new(gin.outputs[f"{side} {channel}"], mix.inputs["B"])
        links.new(level.outputs["Value"], mix.inputs["Factor"])
        out = mix.outputs["Result"]
    links.new(out, gout.inputs["Result"])
    return tree


def _image_node(mat: Any, image: Any, vector: Any = None, pos: Any = None) -> Any:
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.width = 240.0
    if pos is not None:
        tex.location = pos
    if vector is not None:
        mat.node_tree.links.new(vector, tex.inputs["Vector"])
    return tex


def _to_transparency(mat: Any, bsdf: Any, alpha_out: Any, cutout: bool = False) -> Any:
    # dao alpha into transparency socket, cutout clips rather than blends
    links = mat.node_tree.links
    invert = mat.node_tree.nodes.new("ShaderNodeInvert")
    invert.location = _POS_TRANS_INV
    links.new(alpha_out, invert.inputs["Color"])
    links.new(invert.outputs["Color"], bsdf.inputs["Transparency"])
    if cutout:
        mat.blend_method = "CLIP"
        mat.alpha_threshold = 0.5
    else:
        mat.blend_method = "BLEND"
    return invert.outputs["Color"]


# transparent semantics blend, others ignore diffuse alpha
_TRANSPARENT_SEMANTICS = ("alpha", "punchthrough", "addvpunchthrough")
# punchthrough semantics are alpha-tested not blended
_CUTOUT_SEMANTICS = ("punchthrough", "addvpunchthrough")


def _transparent_semantic(mao: dict) -> bool:
    semantic = (mao.get("semantic") or "").lower()
    return semantic.startswith(_TRANSPARENT_SEMANTICS)


def _cutout_semantic(mao: dict) -> bool:
    semantic = (mao.get("semantic") or "").lower()
    return semantic.startswith(_CUTOUT_SEMANTICS)


def _channel_varies(image: Any, channel: int) -> bool:
    # true if channel varies, reads via foreach_get into numpy

    try:
        pixels = image.pixels
        count = len(pixels)
    except (AttributeError, TypeError, ReferenceError):
        return False
    if not count:
        return False
    flat = np.empty(count, dtype=np.float32)
    try:
        pixels.foreach_get(flat)
    except (AttributeError, TypeError, ReferenceError):
        return False
    vals = flat[channel::4]
    return bool(vals.size) and float(vals.max()) - float(vals.min()) > 1e-3


def _has_alpha(image: Any) -> bool:
    return _channel_varies(image, 3)


# node group templates for material types

def _terrain_node_group() -> Any:
    # create or get the terrain material node group.
    #
    # terrain materials use a palette texture for diffuse, with mask channels
    # controlling blending between palette entries. normal map is palette-specific.

    name = "ThedasIO_Terrain"
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree

    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    tag(tree, "node_group")
    
    # inputs
    tree.interface.new_socket("Palette", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("Normal", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("MaskV", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("MaskA", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("MaskA2", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("ReliefMap", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("UVScale", in_out="INPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket("ReliefScale", in_out="INPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket("PaletteParams", in_out="INPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket("PaletteDim", in_out="INPUT", socket_type="NodeSocketVector")
    
    # outputs
    tree.interface.new_socket("Base Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket("Height", in_out="OUTPUT", socket_type="NodeSocketFloat")
    
    nodes = tree.nodes
    links = tree.links
    
    gin = nodes.new("NodeGroupInput")
    gin.location = (-1200.0, 0.0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (800.0, 0.0)
    
    # palette texture -> Base Color (palette is the diffuse)
    # the palette texture contains multiple color entries; UV determines which entry
    # for now, pass through directly; actual palette indexing is done in shader
    links.new(gin.outputs["Palette"], gout.inputs["Base Color"])
    
    # normal map
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.location = (0.0, -200.0)
    nmap.width = 160.0
    links.new(gin.outputs["Normal"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], gout.inputs["Normal"])
    
    # height from relief map (red channel)
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (0.0, 200.0)
    links.new(gin.outputs["ReliefMap"], sep.inputs["Color"])
    links.new(sep.outputs["Red"], gout.inputs["Height"])
    
    return tree


def _water_node_group() -> Any:
    # create or get the water material node group
    # 
    # water materials use normal map for distortion, decal for surface detail,
    # mask for foam/edges, and vector parameters for wave animation
    
    name = "ThedasIO_Water"
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree

    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    tag(tree, "node_group")
    
    # inputs
    tree.interface.new_socket("Normal", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("Decal", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("Mask", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("WaveParams", in_out="INPUT", socket_type="NodeSocketVector")  # 12 floats packed
    tree.interface.new_socket("WaterColor", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("WaterVisual", in_out="INPUT", socket_type="NodeSocketVector")  # 4 floats
    
    # outputs
    tree.interface.new_socket("Base Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket("Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket("Alpha", in_out="OUTPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket("Emission", in_out="OUTPUT", socket_type="NodeSocketColor")
    
    nodes = tree.nodes
    links = tree.links
    
    gin = nodes.new("NodeGroupInput")
    gin.location = (-1200.0, 0.0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (800.0, 0.0)
    
    # water color as base
    links.new(gin.outputs["WaterColor"], gout.inputs["Base Color"])
    
    # normal map for water distortion
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.location = (0.0, -200.0)
    nmap.width = 160.0
    links.new(gin.outputs["Normal"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], gout.inputs["Normal"])
    
    # mask alpha for transparency/foam
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (0.0, 200.0)
    links.new(gin.outputs["Mask"], sep.inputs["Color"])
    links.new(sep.outputs["Alpha"], gout.inputs["Alpha"])
    
    # emission from decal (specular highlights)
    links.new(gin.outputs["Decal"], gout.inputs["Emission"])
    
    return tree


def _character_node_group() -> Any:
    # create or get the character material node group.
    
    # extended character shader with age maps, tattoo, and brow stubble.

    # for now, the standard character wiring in _wire_material handles this
    # this is a placeholder
    return None


def get_material_node_group(material_type: str) -> Any:
    # get the appropriate node group for a material type
    if material_type == "terrain":
        return _terrain_node_group()
    elif material_type == "water":
        return _water_node_group()
    elif material_type == "character":
        return _character_node_group()
    return None
