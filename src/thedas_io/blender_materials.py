"""Build Blender materials from DAO MAOs: channel packing, tints, transparency."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
import numpy as np

from .datablocks import tag
from .export import _LOADED_ITEMS, remember
from .materials import classify_material, parse_mao_bytes, slot_roles, vector_arrays
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

    # build_material classifies the MAO and applies the matching node group
    mat = build_material(parsed.get("name") or mao_path.stem, parsed, images, color, tint)

    item["objects"].append(mat)  # live material for mao edit-back
    for obj in objs:
        obj.data.materials.append(mat)
    return mat


def _link(links: Any, from_socket: Any, to_socket: Any) -> None:
    # one link per input: a second chain on the same socket is not an upgrade
    if from_socket is None or to_socket is None or to_socket.is_linked:
        return
    links.new(from_socket, to_socket)


def _link_force(links: Any, from_socket: Any, to_socket: Any) -> None:
    # replace whatever feeds this input (the character group supersedes the
    # plain diffuse/normal chain wired before it)
    if from_socket is None or to_socket is None:
        return
    for link in list(to_socket.links):
        links.remove(link)
    links.new(from_socket, to_socket)


# group parameter sockets driven by MAO Vector4f values: socket -> (name, row)
_GROUP_PARAMS = {
    "ThedasIO_Terrain": {
        "paletteParams": ("mml_vPalette_parameters", 0),
        "paletteDimensions": ("mml_vPalette_dimensions", 0),
    },
    # mat_vVSHWaterParams is the 3-wave vertex shader table, mat_vPSHWaterParams
    # the pixel shader one; both arrive as Vector4fArray extras, not <vector>.
    "ThedasIO_Water": {
        "waveParams": ("mat_vVSHWaterParams", 0),
        "waveParams2": ("mat_vVSHWaterParams", 1),
        "waveParams3": ("mat_vVSHWaterParams", 2),
        "waterVisual": ("mat_vPSHWaterParams", 0),
    },
}

# group input socket that receives a role the group named differently. The
# water group is the only one: DAO calls the slot "normal", DA2 "water normal",
# and both feed the same socket.
_GROUP_INPUT_ALIASES = {
    "ThedasIO_Water": {"waterNormal": "normal"},
}

# water normals are sampled in metres, not in the mesh UV
_WATER_TILE_METERS = 4.0


# group output -> the BSDF sockets it can drive, first match wins
_GROUP_OUTPUTS = {
    "Base Color": ("Base Color",),
    "Normal": ("Normal",),
    "Alpha": ("Alpha", "Transparency"),
    "Emission": ("Emission Color", "Emissive Color"),
    "Height": ("Displacement", "Height"),
}


def _mao_rows(parsed: dict, name: str) -> list:
    # a <vector> is one row, a Vector4fArray extra is many
    rows = (parsed.get("vectors") or {}).get(name)
    if rows and isinstance(rows[0], (int, float)):
        return [rows]
    if rows:
        return list(rows)
    return list(vector_arrays(parsed).get(name) or [])


def _seed_group_params(group_node: Any, parsed: dict) -> None:
    tree = getattr(group_node, "node_tree", None)
    table = _GROUP_PARAMS.get(getattr(tree, "name", ""), {})
    for socket_name, (mao_name, row) in table.items():
        rows = _mao_rows(parsed, mao_name)
        socket = group_node.inputs.get(socket_name)
        if socket is None or row >= len(rows) or len(rows[row]) < 3:
            continue
        try:
            socket.default_value = tuple(float(v) for v in rows[row][:3])
        except (TypeError, ValueError):
            continue


def _water_seconds(mat: Any) -> Any:
    nodes = mat.node_tree.nodes
    clock = nodes.get("Water seconds")
    if clock is not None:
        return clock
    clock = nodes.new("ShaderNodeValue")
    clock.name = "Water seconds"
    clock.location = (-1400.0, -420.0)
    # a simple expression evaluates without enabling Python auto-run
    driver = clock.outputs[0].driver_add("default_value").driver
    driver.expression = "frame / 24"
    return clock


def _water_coord(mat: Any) -> Any:
    # water is a flat plane, so a planar object-space lookup is both isotropic
    # and independent of the mesh UV layout
    nodes = mat.node_tree.nodes
    coord = nodes.get("Water coords")
    if coord is None:
        coord = nodes.new("ShaderNodeTexCoord")
        coord.name = "Water coords"
        coord.location = (-1760.0, -240.0)
    return coord


def _water_layer(mat: Any, tex_node: Any, wave: Any, tag: str) -> None:
    # tile one normal sampler and drift it at that wave row's speed. The
    # mapping sits between the coordinate source and the sampler: sampling
    # happens outside the group, so scrolling has to happen here too.
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    mapping = nodes.new("ShaderNodeMapping")
    mapping.name = f"Water UV {tag}".strip()
    mapping.label = "water tiling"
    mapping.location = (tex_node.location.x - 260.0, tex_node.location.y)
    tile = 1.0 / _WATER_TILE_METERS
    mapping.inputs["Scale"].default_value = (tile, tile, 1.0)

    links.new(_water_coord(mat).outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

    if len(wave) < 2 or not any(wave[:2]):
        return
    drift = nodes.new("ShaderNodeVectorMath")
    drift.operation = "SCALE"
    drift.name = f"Water drift {tag}".strip()
    drift.location = (mapping.location.x - 200.0, mapping.location.y)
    drift.inputs["Vector"].default_value = (float(wave[0]), float(wave[1]), 0.0)
    links.new(_water_seconds(mat).outputs[0], drift.inputs["Scale"])
    links.new(drift.outputs["Vector"], mapping.inputs["Location"])


def _scroll_water_uv(mat: Any, tex_node: Any, parsed: dict, group_node: Any) -> None:
    # every mat_vVSHWaterParams row is one wave layer: sample the same normal
    # sheet three times, each drifting its own way, so the ripples interfere
    waves = _mao_rows(parsed, "mat_vVSHWaterParams")
    _water_layer(mat, tex_node, waves[0] if waves else (), "")
    image = tex_node.image
    for index, wave in enumerate(waves[1:3], start=2):
        socket = group_node.inputs.get(f"normal{index}")
        factor = group_node.inputs.get(f"normal{index}Factor")
        if socket is None or image is None:
            continue
        layer = _image_node(
            mat,
            image,
            None,
            (tex_node.location.x, tex_node.location.y - 260.0 * (index - 1)),
        )
        _water_layer(mat, layer, wave, str(index))
        mat.node_tree.links.new(layer.outputs["Color"], socket)
        if factor is not None:
            factor.default_value = 1.0


def _apply_node_group(mat: Any, node_group: Any, parsed: dict, images: dict) -> None:
    # wire a classified MAO into its type's group
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    group_name = getattr(node_group, "name", "")
    aliases = _GROUP_INPUT_ALIASES.get(group_name, {})

    group_node = None
    for node in nodes:
        if node.type == "GROUP" and node.node_tree == node_group:
            group_node = node
            break
    if group_node is None:
        group_node = nodes.new("ShaderNodeGroup")
        group_node.node_tree = node_group
        group_node.location = (500.0, 0.0)
    _seed_group_params(group_node, parsed)

    roles = slot_roles(parsed.get("textures"))
    normal_tex = None
    for semantic, image in images.items():
        role = roles.get(semantic)
        if role is None:
            continue
        socket_name = role if group_node.inputs.get(role) is not None else aliases.get(role)
        socket = group_node.inputs.get(socket_name) if socket_name else None
        if socket is None:
            continue
        tex_node = None
        for node in nodes:
            if node.type == "TEX_IMAGE" and node.image == image:
                tex_node = node
                break
        if tex_node is None:
            tex_node = _image_node(mat, image, role=role)
        _link(links, tex_node.outputs["Color"], socket)
        if socket_name == "normal":
            normal_tex = tex_node

    if normal_tex is not None and group_name == "ThedasIO_Water":
        _scroll_water_uv(mat, normal_tex, parsed, group_node)

    bsdf_nodes = [node for node in nodes if node.type in ("EEVEE_SPECULAR", "BSDF_PRINCIPLED")]
    # a group whose base colour is fed by nothing (an eye MAO classifies as
    # character but carries no face maps) must not override the diffuse chain
    # that is already wired: it would paint the default white over it
    feed = group_node.inputs.get("Base Color")
    if feed is not None and not feed.is_linked:
        return
    for bsdf in bsdf_nodes:
        for out_name, candidates in _GROUP_OUTPUTS.items():
            out = group_node.outputs.get(out_name)
            if out is None:
                continue
            for in_name in candidates:
                target = bsdf.inputs.get(in_name)
                if target is not None:
                    # the group supersedes the plain chain: a water normal map
                    # that also feeds the BSDF directly leaves the group's
                    # Normal output dangling
                    _link_force(links, out, target)
                    break


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
    # specular-bsdf wiring from dao channel packing, then the type's group
    mat: Any = bpy.data.materials.new(name)
    tag(mat, "material")
    mat.use_nodes = True
    _wire_material(mat, mao, images, color=color, tint=tint)

    node_group = get_material_node_group(classify_material(mao))
    if node_group is not None:
        _apply_node_group(mat, node_group, mao, images)

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
        if role == "packed" or (role == "diffuse" and "pack" in str(semantic).lower()):
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
    # the game reads roughness out of the specular map's alpha; when there is no
    # spec map the preview uses this flat value (was the node's 0.2 default)
    bsdf.inputs["Roughness"].default_value = DEFAULT_ROUGHNESS
    links = mat.node_tree.links
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    # no mapping node: the image is stored the right way up (see _blit), so the
    # raw UV renders correctly *and* texture paint lands where the preview shows
    # it. a scale of (1,-1,1) here un-flipped the render but paint ignores it.
    uvmap = nodes.new("ShaderNodeTexCoord")
    uvmap.location = _POS_UV
    vector = uvmap.outputs["UV"]
    # hair, beard and lash cards are single-sided geometry with alpha edges: the
    # game renders them two-sided and alpha-tested, and culling them punches the
    # rectangular holes seen over a scalp
    label = f"{mao.get('name') or ''} {mao.get('material') or ''}".lower()
    if any(token in label for token in ("_har_", "_brd_", "hair", "ulm")):
        mat.use_backface_culling = False
        mat.show_transparent_back = True
        mat.blend_method = "CLIP"
        mat.alpha_threshold = 0.5
    base_feed = None
    normal_feed = None
    rough_feed = None
    trans_feed = None
    emission_feed = None
    diffuse = by_role.get("diffuse")
    spec = by_role.get("specular")
    if diffuse is not None:
        tex_diffuse = _image_node(mat, diffuse, vector, _POS_TEX["diffuse"], role="diffuse")
        base_feed = tex_diffuse.outputs["Color"]
        # an alpha semantic (lashes, punchthrough) is transparent whatever the
        # image reports: its alpha is the only shape the material has
        if _transparent_semantic(mao):
            trans_feed = _to_transparency(
                mat, bsdf, tex_diffuse.outputs["Alpha"], cutout=_cutout_semantic(mao)
            )
        elif _has_alpha(diffuse) and spec is None:
            # face-style map, alpha is the specular map
            links.new(tex_diffuse.outputs["Alpha"], bsdf.inputs["Specular"])
            # opaque with spec map, alpha is not opacity
    for image in packed.values():
        tex = _image_node(mat, image, vector, _POS_TEX["packed"], role="packed")
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
        tex = _image_node(mat, tint_image, vector, _POS_TEX["tint"], role="tint")
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
        tex = _image_node(mat, normal, vector, _POS_TEX["normal"], role="normal")
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = _POS_NMAP
        nmap.width = 160.0
        links.new(tex.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        normal_feed = nmap.outputs["Normal"]

    # character age/tattoo/brow maps composite through their own group. Each
    # map is optional per MAO, so the group gates it on a factor socket: an
    # unconnected input is a pass-through, not a black multiply. The factors
    # start at 0 and stay there: these maps are alternates a preset blends in,
    # and turning them on for every face paints an old, stubbled, tattooed
    # character on a young clean one.
    character = {
        "ageDiffuse": (age_diffuse, "AgeFactor"),
        "ageNormal": (age_normal, "AgeNormalFactor"),
        "tattoo": (tattoo, "TattooFactor"),
        "browStubble": (brow_stubble, "BrowFactor"),
        "browStubbleNormal": (brow_stubble_normal, "BrowNormalFactor"),
    }
    if base_feed is not None and any(image is not None for image, _ in character.values()):
        group = nodes.new("ShaderNodeGroup")
        group.node_tree = create_character_node_group()
        group.location = (700.0, 150.0)
        group.inputs["Normal"].default_value = (0.0, 0.0, 1.0)
        links.new(base_feed, group.inputs["Base Color"])
        if normal_feed is not None:
            links.new(normal_feed, group.inputs["Normal"])
        for role, (image, factor) in character.items():
            if image is None:
                continue
            if role.endswith("Normal"):
                tex = _image_node(mat, image, vector, (352.6, -300.0), role=role)
                nmap = nodes.new("ShaderNodeNormalMap")
                nmap.location = (560.0, -300.0)
                nmap.width = 160.0
                links.new(tex.outputs["Color"], nmap.inputs["Color"])
                links.new(nmap.outputs["Normal"], group.inputs[role])
            else:
                tex = _image_node(mat, image, vector, (352.6, 150.0), role=role)
                links.new(tex.outputs["Color"], group.inputs[role])
            group.inputs[factor].default_value = 0.0
        base_feed = group.outputs["Base Color"]
        normal_feed = group.outputs["Normal"]
        # the plain diffuse/normal chain already fed the BSDF; the group output
        # supersedes it rather than stacking a second link on the same socket
        _link_force(links, base_feed, bsdf.inputs["Base Color"])
        _link_force(links, normal_feed, bsdf.inputs["Normal"])

    if spec is not None:
        tex = _image_node(mat, spec, vector, _POS_TEX["specular"], role="specular")
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
        tex = _image_node(mat, emission, vector, _POS_TEX["emission"], role="emission")
        links.new(tex.outputs["Color"], bsdf.inputs["Emissive Color"])
        emission_feed = tex.outputs["Color"]

    # cycles output from same maps, export ignores links
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = _POS_PRINCIPLED
    principled.inputs["Roughness"].default_value = DEFAULT_ROUGHNESS
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


def _image_node(mat: Any, image: Any, vector: Any = None, pos: Any = None, role: Any = None) -> Any:
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.width = 240.0
    # the role a preset override names: a .mor/.mop says "mml_tAgeDiffuseMap",
    # and this is how the importer finds the node again without guessing
    if role:
        tex["thedas_io_role"] = role
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


# the game reads roughness out of the specular map's alpha; with no spec map the
# preview uses this flat value (EEVEE's specular node defaults to 0.2)
DEFAULT_ROUGHNESS = 0.7

# transparent semantics blend, others ignore diffuse alpha. lashes are cut-out
# cards in the game and their diffuse alpha is the only opacity they carry.
_TRANSPARENT_SEMANTICS = ("alpha", "punchthrough", "addvpunchthrough", "eyelash")
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


def _new_group(name: str) -> Any:
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    tag(tree, "node_group")
    # the orphan sweep drops zero-user tagged groups; a template has no users
    # until something imports a material of its type
    tree.use_fake_user = True
    return tree


def _group_sockets(tree: Any, inputs: list, outputs: list) -> None:
    for name, kind, *rest in inputs:
        socket = tree.interface.new_socket(name, in_out="INPUT", socket_type=kind)
        if rest:
            socket.default_value = rest[0]
    for name, kind in outputs:
        tree.interface.new_socket(name, in_out="OUTPUT", socket_type=kind)


def create_terrain_node_group() -> Any:
    # terrain MAOs carry the palette as a plain texture plus three mask
    # channels and the palette grid parameters. The masks pick palette entries
    # the engine blends; a preview cannot reproduce that without the terrain
    # shader, so they stay exposed as inputs.
    name = "ThedasIO_Terrain"
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = _new_group(name)
    _group_sockets(
        tree,
        [
            ("palette", "NodeSocketColor"),
            ("normal", "NodeSocketColor"),
            ("height", "NodeSocketColor"),
            ("maskV", "NodeSocketColor"),
            ("maskA", "NodeSocketColor"),
            ("maskA2", "NodeSocketColor"),
            ("paletteParams", "NodeSocketVector"),
            ("paletteDimensions", "NodeSocketVector"),
        ],
        [
            ("Base Color", "NodeSocketColor"),
            ("Normal", "NodeSocketVector"),
            ("Height", "NodeSocketFloat"),
        ],
    )
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-900.0, 0.0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (320.0, 0.0)

    links.new(gin.outputs["palette"], gout.inputs["Base Color"])
    if "normal" in gin.outputs:
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (-220.0, -220.0)
        links.new(gin.outputs["normal"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], gout.inputs["Normal"])
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (-220.0, 200.0)
    links.new(gin.outputs["height"], sep.inputs["Color"])
    links.new(sep.outputs["Red"], gout.inputs["Height"])
    return tree


def create_water_node_group() -> Any:
    # DAO water MAOs only ship a normal map; DA2 adds water parameters. The
    # extra inputs are there for those, and colours fall back to a plain water
    # tint so the material is not black before anything is wired.
    name = "ThedasIO_Water"
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = _new_group(name)
    _group_sockets(
        tree,
        [
            ("normal", "NodeSocketColor"),
            ("normal2", "NodeSocketColor"),
            ("normal3", "NodeSocketColor"),
            ("waterDecal", "NodeSocketColor"),
            ("waterMask", "NodeSocketColor"),
            ("waterColor", "NodeSocketColor", (0.16, 0.32, 0.38, 1.0)),
            ("waveParams", "NodeSocketVector"),
            ("waveParams2", "NodeSocketVector"),
            ("waveParams3", "NodeSocketVector"),
            ("waterVisual", "NodeSocketVector"),
            # the layer factors gate an unwired normal: zero keeps its mix a
            # pass-through instead of averaging in a black normal
            ("normal2Factor", "NodeSocketFloat"),
            ("normal3Factor", "NodeSocketFloat"),
        ],
        [
            ("Base Color", "NodeSocketColor"),
            ("Normal", "NodeSocketVector"),
            ("Alpha", "NodeSocketFloat"),
        ],
    )
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-900.0, 0.0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (520.0, 0.0)

    links.new(gin.outputs["waterColor"], gout.inputs["Base Color"])

    normals = []
    for name in ("normal", "normal2", "normal3"):
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (-520.0, 260.0 - 180.0 * len(normals))
        links.new(gin.outputs[name], nmap.inputs["Color"])
        normals.append(nmap.outputs["Normal"])

    # each wave layer is a weighted normal: multiply by its factor, add, then
    # renormalize. A factor of 0 leaves an unconnected layer out of the sum,
    # where a Mix at factor 1 would have discarded the earlier layers instead.
    acc = normals[0]
    for index, factor in enumerate(("normal2Factor", "normal3Factor"), start=1):
        weight = nodes.new("ShaderNodeVectorMath")
        weight.operation = "SCALE"
        weight.label = f"wave weight {index + 1}"
        weight.location = (-260.0, 60.0 - 120.0 * index)
        links.new(normals[index], weight.inputs["Vector"])
        links.new(gin.outputs[factor], weight.inputs["Scale"])
        total = nodes.new("ShaderNodeVectorMath")
        total.operation = "ADD"
        total.label = f"sum waves 1-{index + 1}"
        total.location = (-60.0, -80.0 - 100.0 * index)
        links.new(acc, total.inputs[0])
        links.new(weight.outputs["Vector"], total.inputs[1])
        acc = total.outputs["Vector"]
    unit = nodes.new("ShaderNodeVectorMath")
    unit.operation = "NORMALIZE"
    unit.label = "renormalize ripples"
    unit.location = (180.0, -240.0)
    links.new(acc, unit.inputs["Vector"])
    links.new(unit.outputs["Vector"], gout.inputs["Normal"])

    lum = nodes.new("ShaderNodeRGBToBW")
    lum.location = (-220.0, 200.0)
    links.new(gin.outputs["waterMask"], lum.inputs["Color"])
    links.new(lum.outputs["Val"], gout.inputs["Alpha"])
    return tree


def create_character_node_group() -> Any:
    # The age/tattoo/brow maps are optional per MAO, and a node group cannot
    # test whether an input is connected. Each one therefore has a factor
    # input defaulting to 0, which makes its Mix a pass-through until
    # _wire_material turns it on for a map that exists.
    name = "ThedasIO_Character"
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = _new_group(name)
    _group_sockets(
        tree,
        [
            ("Base Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
            ("Normal", "NodeSocketVector"),
            ("ageDiffuse", "NodeSocketColor"),
            ("ageNormal", "NodeSocketVector"),
            ("tattoo", "NodeSocketColor"),
            ("browStubble", "NodeSocketColor"),
            ("browStubbleNormal", "NodeSocketVector"),
            ("browColour", "NodeSocketColor", (0.1, 0.07, 0.05, 1.0)),
            ("tattooColour", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)),
            ("AgeFactor", "NodeSocketFloat"),
            ("AgeNormalFactor", "NodeSocketFloat"),
            ("TattooFactor", "NodeSocketFloat"),
            ("BrowFactor", "NodeSocketFloat"),
            ("BrowNormalFactor", "NodeSocketFloat"),
        ],
        [
            ("Base Color", "NodeSocketColor"),
            ("Normal", "NodeSocketVector"),
        ],
    )
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-900.0, 200.0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (520.0, 200.0)

    # the blend skin texture is a second skin map mixed in by "Blend All
    # Override" (mml_fAgeBlendWeight[11]); the tattoo and brow/stubble maps are
    # tint masks that say where their colour goes, not colours of their own
    colour = gin.outputs["Base Color"]
    blend = nodes.new("ShaderNodeMix")
    blend.data_type = "RGBA"
    blend.blend_type = "MIX"
    blend.label = "ageDiffuse"
    links.new(colour, blend.inputs["A"])
    links.new(gin.outputs["ageDiffuse"], blend.inputs["B"])
    links.new(gin.outputs["AgeFactor"], blend.inputs["Factor"])
    colour = blend.outputs["Result"]

    for sample, factor, tint in (
        ("tattoo", "TattooFactor", "tattooColour"),
        ("browStubble", "BrowFactor", "browColour"),
    ):
        level = nodes.new("ShaderNodeMath")
        level.operation = "MULTIPLY"
        level.label = f"{sample} mask"
        links.new(gin.outputs[sample], level.inputs[0])
        links.new(gin.outputs[factor], level.inputs[1])
        mask = nodes.new("ShaderNodeMix")
        mask.data_type = "RGBA"
        mask.blend_type = "MIX"
        mask.label = sample
        links.new(colour, mask.inputs["A"])
        links.new(gin.outputs[tint], mask.inputs["B"])
        links.new(level.outputs["Value"], mask.inputs["Factor"])
        colour = mask.outputs["Result"]
    links.new(colour, gout.inputs["Base Color"])

    normal = gin.outputs["Normal"]
    for sample, factor in (
        ("ageNormal", "AgeNormalFactor"),
        ("browStubbleNormal", "BrowNormalFactor"),
    ):
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "VECTOR"
        mix.blend_type = "MIX"
        mix.label = sample
        links.new(normal, mix.inputs["A"])
        links.new(gin.outputs[sample], mix.inputs["B"])
        links.new(gin.outputs[factor], mix.inputs["Factor"])
        normal = mix.outputs["Result"]
    links.new(normal, gout.inputs["Normal"])
    return tree


def create_material_node_groups() -> list:
    # register-time factory: the Add > Group menu fills from bpy.data
    return [
        create_terrain_node_group(),
        create_water_node_group(),
        create_character_node_group(),
    ]


def get_material_node_group(material_type: str) -> Any:
    # the group a classified MAO uses. "standard" keeps the plain chain
    if material_type == "terrain":
        return create_terrain_node_group()
    if material_type == "water":
        return create_water_node_group()
    if material_type == "character":
        return create_character_node_group()
    return None
