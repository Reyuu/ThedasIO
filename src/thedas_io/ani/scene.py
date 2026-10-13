# Play a decoded `.ani` on an imported MMH armature. A channel quaternion is
# in the bone's parent frame, so a bone's armature-space orientation at a key
# time is the animated rotation composed down the chain, rest standing in for
# bones without a channel. Each key drives pose_bone.matrix to that world
# transform and lets Blender solve the basis.

from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from .description import ROTATION32, ROTATION64, TRANSLATION

BONE_EXPORT_KEY = "thedas_io_xprt"
BONE_NODE_POS = "thedas_io_node_pos"
BONE_NODE_ROT = "thedas_io_node_rot"
SUFFIXES = ("_rotation", "_translation")


def bones_by_export(arm_obj) -> dict:
    out: dict = {}
    for bone in arm_obj.data.bones:
        stamped = bone.get(BONE_EXPORT_KEY) or ""
        for name in str(stamped).split(","):
            if name:
                out[name] = bone.name
    # fall back to stripping the channel suffix when a rig lacks the stamps
    if not out:
        for bone in arm_obj.data.bones:
            key = bone.name.lower()
            for suffix in SUFFIXES:
                key = key.removesuffix(suffix)
            out[key] = bone.name
    return out


def _match(by_export: dict, node_name: str):
    if node_name in by_export:
        return by_export[node_name]
    key = node_name.lower()
    for suffix in SUFFIXES:
        key = key.removesuffix(suffix)
    return by_export.get(key)


def _quat(q_file) -> Quaternion:
    x, y, z, w = q_file
    return Quaternion((w, x, y, z)).normalized()


def _sample(keys: list, time: float):
    if not keys:
        return None
    if time <= keys[0][0]:
        return keys[0][1]
    previous = keys[0]
    for current in keys[1:]:
        t0, v0 = previous
        t1, v1 = current
        if t1 >= time:
            if t1 == t0:
                return v1
            fraction = (time - t0) / (t1 - t0)
            if isinstance(v0, tuple) and len(v0) == 4:
                first = Quaternion((v0[3], v0[0], v0[1], v0[2]))
                second = Quaternion((v1[3], v1[0], v1[1], v1[2]))
                quat = first.slerp(second, max(0.0, min(1.0, fraction)))
                return (quat.x, quat.y, quat.z, quat.w)
            return tuple(v0[i] + (v1[i] - v0[i]) * fraction
                         for i in range(len(v0)))
        previous = current
    return keys[-1][1]


# Modern actions keep their curves in channelbags; action.fcurves is gone in
# 5.x and is a first-slot-only accessor in the 4.4-4.5 window, so walk the bags
# whenever there are any and only fall back to the flat accessor.
def _fcurves(action) -> list:
    out: list = []
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                out.extend(bag.fcurves)
    if out:
        return out
    return list(getattr(action, "fcurves", []))


# A bone's MMH node at rest; unstamped bones fall back to the Blender rest.
def rest_local(arm_obj, name: str) -> tuple:
    bone = arm_obj.data.bones[name]
    pos = bone.get(BONE_NODE_POS)
    rot = bone.get(BONE_NODE_ROT)

    if pos and rot:
        return tuple(float(v) for v in pos[:3]), tuple(float(v) for v in rot)

    rel = bone.matrix_local
    if bone.parent is not None:
        rel = bone.parent.matrix_local.inverted() @ rel
    loc, quat, _scale = rel.decompose()
    return tuple(loc), (quat.x, quat.y, quat.z, quat.w)


# Evaluated in MMH node space, so no frame conversion anywhere: each bone is
# driven to the target world transform the animation implies.
def build_action(anim, arm_obj, fps: float = 30.0) -> bpy.types.Action:
    action = bpy.data.actions.new(str(anim.name or "animation"))
    # keyframe_insert writes into the armature's active action, so it has to
    # be the one we are building before any keys land
    arm_obj.animation_data_create().action = action

    rot_channels: dict = {}
    pos_channels: dict = {}
    by_export = bones_by_export(arm_obj)

    for node in anim.nodes:
        bone = _match(by_export, node.name)
        if bone is None:
            continue
        if node.target in (ROTATION32, ROTATION64):
            rot_channels.setdefault(bone, node)
        elif node.target == TRANSLATION:
            pos_channels.setdefault(bone, node)

    if not rot_channels and not pos_channels:
        return action

    def parent_chain(bone_name: str) -> list:
        out = []
        bone = arm_obj.data.bones.get(bone_name)
        while bone is not None:
            out.append(bone.name)
            bone = bone.parent
        out.reverse()
        return out

    chains = {bone.name: parent_chain(bone.name) for bone in arm_obj.data.bones}

    def local_matrix(name: str, time: float | None = None) -> Matrix:
        pos, rot = rest_local(arm_obj, name)
        pos_channel = pos_channels.get(name)
        rot_channel = rot_channels.get(name)

        if time is not None and pos_channel is not None:
            # a translation channel is an offset on the rest position, in the
            # rest frame (3ds Max importer: rest * translate(p))
            offset = Vector(_sample(pos_channel.keys, time)[:3])
            pos = Vector(pos[:3]) + _quat(rot) @ offset

        if time is not None and rot_channel is not None:
            rot = tuple(_sample(rot_channel.keys, time))
        return Matrix.LocRotScale(tuple(pos[:3]), _quat(rot), (1.0, 1.0, 1.0))

    node_rest = {}
    for bone in arm_obj.data.bones:
        matrix = Matrix.Identity(4)
        for name in chains[bone.name]:
            matrix = matrix @ local_matrix(name)
        node_rest[bone.name] = matrix

    times = sorted({time for node in anim.nodes
                    if node.target in (ROTATION32, ROTATION64, TRANSLATION)
                    for time, _value in node.keys})
    if not times:
        return action

    # parent bones first, so a child solves against its parent's target
    by_depth: dict[int, list[str]] = {}
    for bone in arm_obj.data.bones:
        by_depth.setdefault(len(chains[bone.name]), []).append(bone.name)

    for time in times:
        frame = time * fps
        whole = math.floor(frame)
        bpy.context.scene.frame_set(whole, subframe=frame - whole)  # type: ignore[union-attr]

        for depth in sorted(by_depth):
            for name in by_depth[depth]:
                node_anim = Matrix.Identity(4)
                for ancestor in chains[name]:
                    node_anim = node_anim @ local_matrix(ancestor, time)

                bone_rest = arm_obj.data.bones[name].matrix_local
                target = node_anim @ node_rest[name].inverted() @ bone_rest

                pb = arm_obj.pose.bones[name]
                pb.rotation_mode = "QUATERNION"
                pb.matrix = target
                pb.keyframe_insert("rotation_quaternion", frame=frame, group=name)
                pb.keyframe_insert("location", frame=frame, group=name)
            bpy.context.view_layer.update()  # type: ignore[union-attr]

    # the frames this function keyed on every bone; anything else on the action
    # was added by hand
    action["thedas_io_ani_baked"] = [time * fps for time in times]
    if _fcurves(action):
        try:
            action.frame_range = (0.0, anim.length * fps)
        except AttributeError:
            pass
    return action
