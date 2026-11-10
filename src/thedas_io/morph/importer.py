# a .mor names its parts: dao parts are stems (hf_uhm_basa_0), da2 parts are
# internal paths (art\..\mt_bas_hm). each one is an .mmh, imported on its own,
# then every rig after the first is driven by the first so the parts move as one
# character. node values land on the character material groups where a param
# name matches; whatever has no socket stays on the master armature as json.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bpy

from ..asset_db.scanner import find_exact, find_resource
from ..assets import extract
from ..export import ITEMS


def _local_row(filename: str, preset_row: dict | None) -> dict | None:
    # a preset read from disk resolves its siblings from its own folder
    if not preset_row or preset_row.get("package"):
        return None
    path = Path(preset_row["path"]).parent / filename
    if not path.is_file():
        return None
    return {
        "package": None,
        "filename": filename,
        "ext": Path(filename).suffix,
        "path": str(path),
        "name_hash": None,
    }


def part_row(
    name: str, preset_row: dict | None, db: Any, game: str, local_only: bool
) -> dict | None:
    # dao part names are stems, da2 part names are internal paths without .mmh
    stem = name.replace("\\", "/").rsplit("/", 1)[-1]
    local = _local_row(f"{stem}.mmh".lower(), preset_row)
    if local is not None:
        return local
    if local_only:
        return None
    if "/" in name or "\\" in name:
        return find_resource(name.replace("\\", "/") + ".mmh", db_path=db, game=game)
    return find_exact(f"{stem}.mmh".lower(), db_path=db, game=game)


def tint_path(
    name: str, preset_row: dict | None, db: Any, game: str, local_only: bool
) -> Path | None:
    # a tint slot names a .tnt, the same ten colours a material group takes
    filename = f"{name}.tnt".lower()
    local = _local_row(filename, preset_row)
    if local is not None:
        return Path(local["path"])
    if local_only:
        return None
    hit = find_exact(filename, db_path=db, game=game)
    return extract(hit) if hit is not None else None


def _node_params(preset: Any) -> dict:
    # node name -> vector rows/floats/textures, in the shape the mao uses
    out: dict = {}
    for node in getattr(preset, "nodes", ()):
        vectors = {vec.param: vec.values for vec in node.vectors if vec.values}
        floats = {flt.param: flt.value for flt in node.floats}
        textures = {tex.param: tex.name for tex in node.textures if tex.name}
        if vectors or floats or textures:
            out[node.name] = {"vectors": vectors, "floats": floats, "textures": textures}
    return out


def _mop_params(preset: Any) -> dict:
    # overrides carry explicit row indexes, so they stay sparse: a .mop may set
    # two rows and must not wipe the rest a .mor brought in
    out: dict = {}
    for cell in preset.material_vectors:
        rows = out.setdefault(cell.node, {}).setdefault("vectors", {}).setdefault(cell.param, {})
        rows[cell.index] = cell.value
    for cell in preset.material:
        out.setdefault(cell.node, {}).setdefault("floats", {}).setdefault(cell.param, {})[
            cell.index
        ] = cell.value
    for cell in preset.textures:
        out.setdefault(cell.node, {}).setdefault("textures", {})[cell.param] = cell.name
    return out


def _preset_node(obj_name: str, params: dict) -> str | None:
    # mesh objects are named "<msh prefix>_<node>", the preset names the node
    for name in params:
        if obj_name == name or obj_name.endswith(f"_{name}"):
            return name
    return None


def _push_values(objects: list, params: dict) -> list:
    # a preset's tint rows are the same ten colours a .tnt holds, written into
    # the DAO Tint groups by the tnt field order the exporter already uses
    from ..tnt import NAMES, SIDES, alpha_input, group_name

    wanted = {group_name(side) for side in SIDES}
    touched = []
    for obj in objects:
        name = _preset_node(obj.name, params)
        if name is None:
            continue
        rows = (params[name].get("vectors") or {}).get("mml_vTintMaskColours") or ()
        if not rows:
            continue
        # a .mor writes every row in order, a .mop patches the few it carries
        items = list(enumerate(rows)) if isinstance(rows, list) else sorted(rows.items())
        for mat in getattr(obj.data, "materials", ()):
            tree = getattr(mat, "node_tree", None) if mat is not None else None
            if tree is None:
                continue
            for node in tree.nodes:
                group = getattr(node, "node_tree", None)
                if group is None or group.name not in wanted:
                    continue
                for index, colour in items:
                    if index >= len(NAMES):
                        break
                    socket = node.inputs.get(NAMES[index])
                    if socket is None:
                        continue
                    value = tuple(float(v) for v in colour)
                    socket.default_value = value
                    alpha = node.inputs.get(alpha_input(NAMES[index]) or "")
                    if alpha is not None and len(value) == 4:
                        alpha.default_value = value[3]
                touched.append(f"{obj.name}:{mat.name}")
    return touched


def _texture_res(name: str) -> str:
    # a preset names a texture with or without its .dds
    text = str(name or "")
    stem = text.rsplit(".", 1)[0] if "." in text else text
    return f"{stem}.dds"


def _role_node(mat: Any, role: str) -> Any:
    # the image node that role was tagged with when the material was wired
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    if tree is None:
        return None
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.get("thedas_io_role") == role:
            return node
    return None


def apply_textures(
    objects: list, params: dict, row: dict | None, db: Any, local_only: bool, game: str, emit: Any
) -> tuple[list, list]:
    # a preset's node textures replace the maps the mao picked: skin, hair, and
    # the age/tattoo sets a .mop swaps in
    from ..assets import resolve
    from ..materials import semantic_role
    from ..textures import load_image

    applied, missing = [], []
    for obj in objects:
        name = _preset_node(obj.name, params)
        if name is None:
            continue
        for param, res in (params[name].get("textures") or {}).items():
            role = semantic_role(param)
            if role is None or not res:
                continue
            target = _texture_res(res)
            if row is None:
                path = None
            else:
                path = resolve(target, row, db, local_only, game=game)
            if path is None:
                missing.append(f"{name}:{param} ({target})")
                continue
            image = bpy.data.images.get(Path(path).name) or load_image(path)
            for mat in getattr(obj.data, "materials", ()):
                node = _role_node(mat, role)
                if node is not None:
                    node.image = image
            applied.append(f"{name}:{param}")
    return applied, missing


def _flat(rows: Any, index: int, default: float = 0.0) -> float:
    # a mor gives dense vec4 rows, a mop sparse {row: vec4}; both index the same
    # flattened float array the toolset's morph sliders write
    if isinstance(rows, dict):
        row = rows.get(index // 4)
        if row is None:
            return default
        try:
            return float(row[index % 4])
        except (IndexError, TypeError, ValueError):
            return default
    if not isinstance(rows, (list, tuple)):
        return default
    first, second = divmod(index, 4)
    try:
        return float(rows[first][second])
    except (IndexError, TypeError, ValueError):
        return default


# tools/DefaultSettings.xml maps the morph editor's sliders onto these arrays
AGE_DIFFUSE = 11
AGE_NORMAL = 23


def _push_weights(objects: list, params: dict) -> list:
    # the group holds one factor per map, so brow/stubble takes the strongest
    # region; the game blends each region separately with its own weight
    touched = []
    for obj in objects:
        name = _preset_node(obj.name, params)
        if name is None:
            continue
        vectors = params[name].get("vectors") or {}
        age = vectors.get("mml_fAgeBlendWeight") or ()
        brow = vectors.get("g_vFacialHairWeights") or ()
        tattoo = vectors.get("mml_vTattooParameters") or ()
        colour = vectors.get("g_vFacialHairColour") or ()
        values = {
            "AgeFactor": _flat(age, AGE_DIFFUSE),
            "AgeNormalFactor": _flat(age, AGE_NORMAL),
            "BrowFactor": max((_flat(brow, i) for i in range(8)), default=0.0),
            "TattooFactor": max((_flat(tattoo, i) for i in range(4)), default=0.0),
        }
        if not any(values.values()) and not colour:
            continue
        for mat in getattr(obj.data, "materials", ()):
            tree = getattr(mat, "node_tree", None) if mat is not None else None
            if tree is None:
                continue
            for node in tree.nodes:
                group = getattr(node, "node_tree", None)
                if group is None or group.name != "ThedasIO_Character":
                    continue
                for key, value in values.items():
                    socket = node.inputs.get(key)
                    if socket is not None:
                        socket.default_value = float(value)
                if colour:
                    socket = node.inputs.get("browColour")
                    if socket is not None:
                        socket.default_value = (*tuple(colour[0][:3]), 1.0)
                touched.append(f"{obj.name}:{mat.name}")
    return touched


def _is_node(obj_name: str, node: str) -> bool:
    return obj_name == node or obj_name.endswith(f"_{node}")


def _base_mesh(armature: Any, leaf: str) -> Any:
    for obj in rig_meshes(armature):
        if obj.name == leaf or obj.name.endswith(f"_{leaf}"):
            return obj
    return None


def apply_shapes(
    context: Any,
    armature: Any,
    model_params: Any,
    preset_row: dict | None,
    db: Any,
    local_only: bool,
    game: str,
    emit: Any,
) -> list:
    # a model param names a shape .mmh: the preset's weight becomes a shape key,
    # so Blender's own value slider is the live control the toolset has
    from ..importers import _import_row

    applied = []
    for name, weight in model_params:
        if abs(float(weight)) < 1e-4:
            continue
        row = part_row(name, preset_row, db, game, local_only)
        if row is None:
            emit({"WARNING"}, f"shape {name} not found")
            continue
        result = _import_row(context, row, db, local_only, emit)
        reg = ITEMS.pop(row["filename"], None)
        extra = (
            [obj for obj in list(reg.get("meshes", ())) + [reg.get("armature")] if obj is not None]
            if reg
            else []
        )
        if result != {"FINISHED"} or reg is None:
            continue
        for shape_obj in reg.get("meshes", ()):
            leaf = shape_obj.name.rsplit("_", 1)[-1]
            base = _base_mesh(armature, leaf)
            if base is None:
                emit({"WARNING"}, f"shape {name}: no {leaf} on this rig")
                continue
            if len(base.data.vertices) != len(shape_obj.data.vertices):
                emit(
                    {"WARNING"},
                    f"shape {name}: {leaf} has {len(shape_obj.data.vertices)} verts, "
                    f"the rig has {len(base.data.vertices)}",
                )
                continue
            key = base.shape_key_add(name=f"shape {name}", from_mix=False)
            for index, vertex in enumerate(shape_obj.data.vertices):
                key.data[index].co = vertex.co
            key.value = float(weight)
            applied.append(f"{leaf}:{name}")
        for obj in extra:
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in list(bpy.data.collections):
            if (
                collection.name.startswith(Path(str(row["filename"])).stem)
                and not collection.objects
            ):
                bpy.data.collections.remove(collection)
    return applied


# a tint slot names a .tnt and its prefix says which node it paints; the game
# reads the same ten colours out of the material's mml_vTintMaskColours array
TINT_TARGETS = {
    "skn": ("FaceM1",),
    "eye": ("EyesM1",),
    "har": ("HairM1", "HairM2", "HairM3", "BeardM1", "BeardM2", "BeardM3"),
    "brd": ("BeardM1", "BeardM2", "BeardM3"),
    "ulm": ("LashesM1",),
    "stb": ("FaceM1", "HairM1", "HairM2", "HairM3"),
}


def tint_targets(name: str) -> tuple:
    # t1_skn_001 -> "skn"; the prefix is the second field of the slot name
    parts = str(name or "").lower().split("_")
    for token in parts[1:2]:
        if token in TINT_TARGETS:
            return TINT_TARGETS[token]
    return ()


def tint_rows(path: Any) -> list:
    # a .tnt is the same ten colours in the same order as the material array
    from ..tnt import NAMES, parse

    values = parse(Path(path).read_bytes())
    return [tuple(float(v) for v in values[name]) for name in NAMES]


def _apply_rows(obj: Any, rows: list) -> int:
    from ..tnt import NAMES, SIDES, alpha_input, group_name

    wanted = {group_name(side) for side in SIDES}
    touched = 0
    for mat in getattr(obj.data, "materials", ()):
        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None:
            continue
        for node in tree.nodes:
            group = getattr(node, "node_tree", None)
            if group is None or group.name not in wanted:
                continue
            for index, colour in enumerate(rows):
                socket = node.inputs.get(NAMES[index])
                if socket is None:
                    continue
                socket.default_value = colour
                alpha = node.inputs.get(alpha_input(NAMES[index]) or "")
                if alpha is not None and len(colour) == 4:
                    alpha.default_value = colour[3]
            touched += 1
    return touched


def apply_tint_files(armature: Any, tints: list, emit: Any) -> list:
    # every slot the preset names is loaded and painted onto its node, so a
    # preset that only references tints (a .mop) still gets the colours
    from ..tnt import NAMES

    meshes = rig_meshes(armature)
    applied = []
    for tint in tints:
        if not tint.get("file"):
            continue
        rows = tint_rows(tint["file"])
        for node in tint_targets(tint["name"]):
            for obj in meshes:
                if not _is_node(obj.name, node):
                    continue
                _apply_rows(obj, rows)
                if tint["name"].lower().startswith("t1_stb_"):
                    for mat in getattr(obj.data, "materials", ()):
                        tree = getattr(mat, "node_tree", None)
                        if tree is None:
                            continue
                        for group_node in tree.nodes:
                            group = getattr(group_node, "node_tree", None)
                            if group is None or group.name != "ThedasIO_Character":
                                continue
                            socket = group_node.inputs.get("browColour")
                            if socket is not None:
                                socket.default_value = rows[NAMES.index("Diffuse R")]
                applied.append(f"{node}:{tint['name']}")
    return applied


def rig_meshes(armature: Any) -> list:
    # every mesh this rig deforms, including parts adopted from another rig
    out = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if any(
            m.type == "ARMATURE" and getattr(m, "object", None) is armature for m in obj.modifiers
        ):
            out.append(obj)
    return out


def _assemble(master: Any, others: list) -> None:
    # one rig drives every part: retarget the modifiers, then hide each part's
    # own rig but keep it parented so the whole character follows the master
    for arm in others:
        for obj in bpy.data.objects:
            for mod in getattr(obj, "modifiers", ()):
                if mod.type == "ARMATURE" and mod.object is arm:
                    mod.object = master
        arm.parent = master
        arm.hide_viewport = True
        arm.hide_render = True


def _tints(preset: Any, preset_row: dict | None, db: Any, game: str, local_only: bool) -> list:
    out = []
    for index, tint in enumerate(preset.tints):
        if not tint.name:
            continue
        path = tint_path(tint.name, preset_row, db, game, local_only)
        out.append({"index": index, "name": tint.name, "file": str(path) if path else ""})
    return out


def import_preset(
    context: Any,
    preset: Any,
    preset_row: dict | None,
    db: Any,
    local_only: bool,
    emit: Any,
) -> Any:
    from ..importers import _import_row

    game = getattr(context.scene, "thedas_io_game", "DAO")
    parts, imported, missing = [], [], []
    for index, part in enumerate(preset.parts):
        if not part.name:
            continue
        row = part_row(part.name, preset_row, db, game, local_only)
        if row is None:
            missing.append(part.name)
            parts.append({"index": index, "name": part.name, "file": "", "objects": []})
            continue
        result = _import_row(context, row, db, local_only, emit)
        reg = ITEMS.get(row["filename"]) or {}
        arm = reg.get("armature")
        parts.append(
            {
                "index": index,
                "name": part.name,
                "file": row["filename"],
                "objects": [obj.name for obj in reg.get("meshes", ())],
                "armature": getattr(arm, "name", ""),
            }
        )
        if result == {"FINISHED"} and arm is not None:
            imported.append(reg)
        else:
            missing.append(part.name)

    armatures = [reg["armature"] for reg in imported]
    master = armatures[0] if armatures else None
    touched: list = []
    textured: list = []
    missing_textures: list = []
    tints = _tints(preset, preset_row, db, game, local_only)
    params = _node_params(preset)
    weighted: list = []
    tinted: list = []
    if master is not None:
        _assemble(master, armatures[1:])
        meshes = rig_meshes(master)
        tinted = apply_tint_files(master, tints, emit)
        touched = _push_values(meshes, params)
        weighted = _push_weights(meshes, params)
        textured, missing_textures = apply_textures(
            meshes, params, preset_row, db, local_only, game, emit
        )
        master["thedas_io_preset"] = str(preset.name or "")
        master["thedas_io_preset_parts"] = json.dumps(parts)
        master["thedas_io_preset_tints"] = json.dumps(tints)
        master["thedas_io_preset_values"] = json.dumps(params)

    emit(
        {"INFO"},
        f"{preset.name}: {len(imported)} parts on one rig, {len(tinted)} tint slots "
        f"loaded, {len(touched)} tint rows, {len(textured)} texture overrides, "
        f"{len(weighted)} blended materials",
    )
    for name in dict.fromkeys(missing):
        emit({"WARNING"}, f"{preset.name}: part {name} not found")
    warned: set = set()
    for tint in tints:
        if tint["file"] or tint["name"] in warned:
            continue
        warned.add(tint["name"])
        emit({"WARNING"}, f"{preset.name}: tint {tint['name']} has no .tnt")
    for item in missing_textures:
        emit({"WARNING"}, f"{preset.name}: texture {item} not found")
    if master is None:
        emit({"WARNING"}, f"{preset.name}: no part could be imported")
        return {"CANCELLED"}
    return {"FINISHED"}


def apply_mop(
    context: Any,
    preset: Any,
    preset_row: dict | None,
    armature: Any,
    db: Any,
    local_only: bool,
    emit: Any,
) -> Any:
    game = getattr(context.scene, "thedas_io_game", "DAO")
    params = _mop_params(preset)
    meshes = rig_meshes(armature)
    tinted = apply_tint_files(armature, _tints(preset, preset_row, db, game, local_only), emit)
    touched = _push_values(meshes, params)
    weighted = _push_weights(meshes, params)
    shapes = apply_shapes(
        context,
        armature,
        [(item.model, item.value) for item in getattr(preset, "model", ())],
        preset_row,
        db,
        local_only,
        game,
        emit,
    )
    textured, missing_textures = apply_textures(
        meshes, params, preset_row, db, local_only, game, emit
    )
    armature["thedas_io_mop"] = str(preset.name or "")
    armature["thedas_io_mop_values"] = json.dumps(params)
    tints = _tints(preset, preset_row, db, game, local_only)
    if tints:
        armature["thedas_io_mop_tints"] = json.dumps(tints)
    emit(
        {"INFO"},
        f"{preset.name}: {len(preset.material_vectors)} colour rows, "
        f"{len(preset.material)} scalars, {len(tinted)} tint slots loaded, "
        f"{len(textured)} texture overrides, {len(touched)} tint rows, "
        f"{len(weighted)} blended materials, {len(shapes)} shape keys",
    )
    for tint in tints:
        if not tint["file"]:
            emit({"WARNING"}, f"{preset.name}: tint {tint['name']} has no .tnt")
    for item in missing_textures:
        emit({"WARNING"}, f"{preset.name}: texture {item} not found")
    return {"FINISHED"}


def preset_armature(context: Any) -> Any:
    # the active rig when it carries a preset, else the first one that does
    active = getattr(context, "active_object", None)
    if (
        active is not None
        and active.type == "ARMATURE"
        and active.get("thedas_io_preset") is not None
    ):
        return active
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE" and obj.get("thedas_io_preset") is not None:
            return obj
    return None
