"""Build Blender objects from decoded MSH chunks and MMH hierarchies."""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Matrix, Quaternion

from .datablocks import tag
from .export import remember
from .geometry import (
    bone_frame,
    bone_names,
    decode_chunk,
    flatten,
    node_local,
    node_paths,
    weld_positions,
)

# a bone with no child bone has no rig data to size it from
_LEAF_BONE_LEN = 0.02


def build_hierarchy(context, tree, chunks):
    # armature from nodes, empties for hooks, skinned chunks on armature

    arm_data = bpy.data.armatures.new(tree.name or "skeleton")
    tag(arm_data, "armature")
    arm_obj = bpy.data.objects.new(arm_data.name, arm_data)
    context.collection.objects.link(arm_obj)
    arm_obj.select_set(True)
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    hosts: dict = {}
    live_names: dict = {}
    pending: list = []  # attach after object mode
    stats: dict = {"bones": 0, "empties": 0, "linked": [], "missing": []}
    try:
        names = bone_names(tree)
        rows = flatten(tree)
        parents = {id(n): p for n, p, _, _ in rows}
        paths = {id(n): path for n, path in node_paths(tree)}
        lengths = _bone_lengths(rows)

        def host_of(node, want_bone=False):
            ancestor = parents[id(node)]
            while ancestor is not None and id(ancestor) not in hosts:
                ancestor = parents[id(ancestor)]
            while ancestor is not None:
                h = hosts.get(id(ancestor))
                if h is not None and (not want_bone or h[0] == "bone"):
                    return h
                ancestor = parents[id(ancestor)]
            return None

        for node, _, loc, quat in rows:
            host = host_of(node, want_bone=(node.kind == "node"))
            if node.kind == "node":
                bone = arm_data.edit_bones.new(node.name or "bone")
                _bx, axis, bone_z = bone_frame(quat)
                bone.head = loc
                length = lengths.get(id(node), _LEAF_BONE_LEN)
                bone.tail = (
                    loc[0] + axis[0] * length,
                    loc[1] + axis[1] * length,
                    loc[2] + axis[2] * length,
                )
                # leaving roll to Blender is what made the face and claw bones
                # look twisted; the node's own rotation says where Z should go
                bone.align_roll(bone_z)
                if host is not None:
                    bone.parent = host[1]
                hosts[id(node)] = ("bone", bone)
                live_names[id(node)] = bone.name  # editbones die with edit mode
                stats["bones"] += 1
            elif node.kind in ("crst", "usrp"):
                empty = bpy.data.objects.new(node.name or "hook", None)
                context.collection.objects.link(empty)
                hosts[id(node)] = ("object", empty)
                bone_name = _edit_bone_name(host)
                host_obj = host[1] if host is not None and host[0] == "object" else None
                pending.append((node, loc, quat, empty, bone_name, host_obj))
                stats["empties"] += 1
            elif node.kind == "mshh":
                chunk = chunks.get(node.values.get(6006))
                if chunk is None:
                    stats["missing"].append(node.name)
                    continue
                hosts[id(node)] = ("object", chunk)
                pending.append((node, loc, quat, chunk, None, None))
                stats["linked"].append(node.name)

    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    # stamp the export names before the anim importer arrives: each bone's
    # "xprt" children carry the animation channel names (.chest_rotation etc.)
    # the game drives that bone with
    for node, _, _, _ in rows:
        if node.kind != "node":
            continue
        bone = arm_obj.data.bones.get(node.name)
        if bone is None:
            continue
        exports = [
            str(c.values.get(6052))
            for c in node.children
            if c.kind == "xprt" and c.values.get(6052)
        ]
        if exports:
            bone["thedas_io_xprt"] = ",".join(exports)
        # the node's own parent-relative transform: the animation value at rest,
        # so the anim importer can subtract it (pose = rest^-1 * channel)
        bn_pos, bn_rot = node_local(node.values)
        if bn_rot:
            bone["thedas_io_node_rot"] = tuple(float(v) for v in bn_rot)
        if bn_pos:
            bone["thedas_io_node_pos"] = tuple(float(v) for v in bn_pos)

    # bone names stable in object mode, attach hooks then snapshot rest

    for node, loc, quat, ref, bone_name, host_obj in pending:
        if node.kind in ("crst", "usrp"):
            _attach_object(ref, arm_obj, bone_name, host_obj)
            ref.matrix_world = (
                Matrix.Translation(loc)
                @ Quaternion((quat[3], quat[0], quat[1], quat[2])).to_matrix().to_4x4()
            )
        else:  # mshh skinned, modifier deforms, object stays at identity
            ref.parent = arm_obj
            ref.matrix_world = Matrix.Identity(4)
            _bind_skin(ref, names, arm_obj, node)

    stats["hosts"] = {}
    for node, _, loc, quat in rows:
        entry = hosts.get(id(node))
        path = paths.get(id(node))
        if entry is None or path is None:
            continue
        kind, ref = entry
        if kind == "bone":
            obj = arm_data.bones.get(live_names[id(node)])
            if obj is not None:
                # bone refs die on mode switch, export re-finds by tag
                pose_bones: Any = getattr(arm_obj.pose, "bones", None)
                pose = None if pose_bones is None else pose_bones.get(obj.name)
                if pose is not None:
                    pose["thedas_io_node"] = obj.name
                stats["hosts"][path] = (kind, (arm_obj, obj.name), obj.matrix_local.copy())
        elif node.kind in ("crst", "usrp"):
            ref.matrix_world = (
                Matrix.Translation(loc)
                @ Quaternion((quat[3], quat[0], quat[1], quat[2])).to_matrix().to_4x4()
            )
            stats["hosts"][path] = (kind, ref, ref.matrix_world.copy())
        else:  # mshh verts are world-space, keep object at identity
            ref.matrix_world = Matrix.Identity(4)

    stats["armature"] = arm_obj
    return stats


def _edit_bone_name(host):
    # bone name while editbone ref still valid
    if host is None or host[0] != "bone":
        return None
    try:
        return host[1].name
    except ReferenceError:
        return None


def _attach_object(obj, arm_obj, bone_name=None, host_obj=None):
    # parent rigid hook to host empty, bone, else armature
    if host_obj is not None:
        try:
            obj.parent = host_obj
            return
        except ReferenceError:
            pass
    if bone_name is not None and bone_name in arm_obj.data.bones:
        obj.parent = arm_obj
        obj.parent_type = "BONE"
        obj.parent_bone = bone_name
    else:
        obj.parent = arm_obj


def _bone_lengths(rows) -> dict:
    heads = {id(n): loc for n, _, loc, _ in rows}
    out: dict = {}
    for node, _, loc, _ in rows:
        child = next((c for c in node.children if c.kind == "node"), None)
        if child is None:
            continue
        span = math.dist(loc, heads[id(child)])
        if span > 1e-6:
            out[id(node)] = span
    return out


def _bind_skin(chunk, names, arm_obj, node=None):
    # rename bone groups to bone names, tag index map, add modifier
    order = (node.values.get(6255) if node is not None else None) or []
    if not isinstance(order, list):
        order = []

    bone_map = {}
    for group in chunk.vertex_groups:
        if group.name.startswith("bone_"):
            local = int(group.name[5:])
            glob = (
                order[local] if 0 <= local < len(order) and isinstance(order[local], int) else local
            )
            if glob in names:
                group.name = names[glob]
            bone_map[group.name] = local

    chunk["thedas_io_bone_index"] = bone_map
    if chunk.modifiers.get("Armature") is None:
        mod = chunk.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj


def _unit_normal(n) -> tuple:
    x, y, z = (float(c) for c in n)
    length = math.sqrt(x * x + y * y + z * z)
    if not math.isfinite(length) or length < 1e-6:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def build_chunk_object(context, mesh, chunk, raw, source, weld=True):
    # one blender object per chunk, registers source msh for export

    build = decode_chunk(mesh, chunk)
    if weld:
        # weld co-located rows so edits move surface without cracking seams
        positions, row_to_vert = weld_positions(build["positions"])
    else:
        # every MSH row stays its own vertex; export takes the legacy 1:1 path
        positions, row_to_vert = build["positions"], None
    rows = build["triangles"]
    if row_to_vert is not None:
        rows_v = [tuple(row_to_vert[v] for v in tri) for tri in rows]
    else:
        rows_v = rows
    # welding can collapse a triangle onto itself. from_pydata drops those
    # silently, so the per-corner normals and UVs would run past the mesh's
    # loops (the access violation in mesh_normals_corner_custom_set). Drop them
    # here, so every per-corner array is built from the faces Blender keeps.
    keep = [i for i, tri in enumerate(rows_v) if len(set(tri)) == 3]
    tris = [rows_v[i] for i in keep]
    corners = [rows[i] for i in keep]

    data = bpy.data.meshes.new(chunk.name or "chunk")
    tag(data, "mesh")
    data.from_pydata(positions, [], tris)

    # a zero or non-finite normal in the file crashes Blender's custom-normal
    # setter, so those corners fall back to +Z
    if build["normals"] and len(data.loops) == 3 * len(corners):
        data.normals_split_custom_set(
            [_unit_normal(build["normals"][v]) for tri in corners for v in tri]
        )

    for i, (uidx, coords) in enumerate(sorted(build["uvs"].items())):
        layer = data.uv_layers.new(name="UVMap" if i == 0 else f"UVMap.{i:03d}")
        if layer is None:
            continue
        layer.data.foreach_set("uv", [c for tri in corners for v in tri for c in coords[v]])
    data.update()
    obj = bpy.data.objects.new(data.name, data)
    context.collection.objects.link(obj)

    if len(positions) != len(build["positions"]):
        obj["thedas_io_row_to_vert"] = row_to_vert

    for bone, members in build["groups"].items():
        group = obj.vertex_groups.new(name=f"bone_{bone}")
        for vidx, weight in members:
            group.add([row_to_vert[vidx] if row_to_vert else vidx], weight, "REPLACE")
    remember(source, "msh", mesh, raw)["objects"].append((obj, chunk))
    return obj
