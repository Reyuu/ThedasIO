# Edit an armature pose back into a `.ani` (the inverse of build_action).
# A node whose key times did not change keeps its key list and only the keys
# that moved are re-encoded; a node that gained keys gets a new key list; a
# keyframe past the end of the clip makes the clip longer, which rewrites
# every node's key list, since key times are fractions of the length.
# Nodes nobody edited keep their original bytes.

from __future__ import annotations

import math
import re
from typing import Any

from mathutils import Matrix, Vector

from .description import ROTATION32, ROTATION64, TRANSLATION, ANIReader, patch_ani
from .scene import _fcurves, _match, _quat, bones_by_export, rest_local

# Blender holds the pose in float32, so an unedited key reads back up to
# ~7.5e-4 off (measured on c_dog.sit_p.ani). Below this the file's own value
# is kept, so edits smaller than ~0.1 degree are dropped.
ROT_TOL = 2e-3  # radians
POS_TOL = 2e-3  # rig units
TIME_TOL = 1e-3  # frames: keyframes this close count as one
_BONE = re.compile(r'pose\.bones\["(.+)"\]')


def _node_rests(arm: Any) -> dict:
    out = {}

    for bone in arm.data.bones:
        chain, walk = [], bone
        while walk is not None:
            chain.append(walk.name)
            walk = walk.parent

        matrix = Matrix.Identity(4)
        for name in reversed(chain):
            pos, rot = rest_local(arm, name)
            matrix = matrix @ Matrix.LocRotScale(pos, _quat(rot), (1.0, 1.0, 1.0))
        out[bone.name] = matrix
    return out


def _locals_at(context: Any, arm: Any, rests: dict, frame: float) -> dict:
    whole = math.floor(frame)
    context.scene.frame_set(whole, subframe=frame - whole)
    context.view_layer.update()

    node_anim = {}
    for bone in arm.data.bones:
        pose_bone = arm.pose.bones[bone.name]
        # pose world = node_anim @ node_rest^-1 @ bone_rest (see build_action)
        node_anim[bone.name] = pose_bone.matrix @ bone.matrix_local.inverted() @ rests[bone.name]

    local = {}
    for bone in arm.data.bones:
        mine = node_anim[bone.name]
        if bone.parent is not None:
            mine = node_anim[bone.parent.name].inverted() @ mine
        local[bone.name] = mine
    return local


# Keyframes build_action did not bake came from the user.
def _hand_keys(action: Any, fps: float) -> dict:
    if action is None:
        return {}

    baked = [float(frame) for frame in action.get("thedas_io_ani_baked", [])]
    out: dict = {}

    for fcurve in _fcurves(action):
        match = _BONE.search(fcurve.data_path)
        if match is None:
            continue
        for point in fcurve.keyframe_points:
            frame = float(point.co[0])
            if any(abs(frame - baked_frame) < TIME_TOL for baked_frame in baked):
                continue
            out.setdefault(match.group(1), set()).add(frame / fps)

    return {bone: sorted(times) for bone, times in out.items()}


# q and -q are the same rotation, so match the file's sign to keep q and -q
# from being written back as a flip.
def _same_sign(new: Any, old: Any, is_rot: bool) -> Any:
    if is_rot and sum(a * b for a, b in zip(new, old)) < 0:
        return tuple(-c for c in new)
    return new


def _differs(new: Any, old: Any, is_rot: bool) -> bool:
    if is_rot:
        dot = abs(sum(a * b for a, b in zip(new, old)))
        return 2 * math.acos(min(1.0, dot)) >= ROT_TOL
    return (Vector(new) - Vector(old)).length >= POS_TOL


def export_action(context: Any, arm: Any, raw: bytes, fps: float) -> bytes:
    scene = context.scene
    keep = (scene.frame_current, scene.frame_subframe)
    bone_names = bones_by_export(arm)
    node_rests = _node_rests(arm)
    action = None
    if arm.animation_data:
        action = arm.animation_data.action
    hand_keys = _hand_keys(action, fps)
    clip = ANIReader(raw).animation
    latest = max((t for times in hand_keys.values() for t in times), default=0.0)
    new_length = max(clip.length, latest)
    extending = new_length > clip.length
    cache: dict = {}

    def locals_for(time: float) -> dict:
        if time not in cache:
            cache[time] = _locals_at(context, arm, node_rests, time * fps)
        return cache[time]

    def value_at(node: Any, time: float, bone: str) -> Any:
        rest_pos, rest_rot = rest_local(arm, bone)
        local = locals_for(time)[bone]
        if node.target == TRANSLATION:
            offset = _quat(rest_rot).inverted() @ (local.to_translation() - Vector(rest_pos))
            return tuple(offset)
        quat = local.to_quaternion()
        return (quat.x, quat.y, quat.z, quat.w)

    def values(node: Any) -> Any:
        own_times = [time for time, _ in node.keys]

        # vfx channels are not bone-driven, so nothing here can edit them; a
        # longer clip still needs their key times rescaled
        if node.target not in (ROTATION32, ROTATION64, TRANSLATION):
            if extending:
                return own_times, [v for _, v in node.keys]
            return None

        is_rot = node.target != TRANSLATION
        bone = _match(bone_names, node.name)

        # unmapped nodes only re-encode when the clip grows
        if bone is None:
            if extending:
                return own_times, [v for _, v in node.keys]
            return None

        added = [
            time
            for time in hand_keys.get(bone, [])
            if all(abs(time - own) > TIME_TOL / fps for own in own_times)
        ]
        times = sorted(set(own_times) | set(added))
        old_at = dict(node.keys)

        # None means "this key already matches the file, leave its bytes alone"
        planned = []
        for time in times:
            new = value_at(node, time, bone)
            old = old_at.get(time)
            if old is None:
                planned.append(new)
                continue

            new = _same_sign(new, old, is_rot)
            if _differs(new, old, is_rot):
                planned.append(new)
            else:
                planned.append(None)

        # same key times, so keep the key list and write only what moved
        if not extending and times == own_times:
            if any(v is not None for v in planned):
                return own_times, planned
            return None

        # a rebuilt key list needs a value at every time
        filled = []
        for time, value in zip(times, planned):
            if value is None:
                filled.append(old_at[time])
            else:
                filled.append(value)
        return times, filled

    new_length_arg = None
    if extending:
        new_length_arg = new_length

    try:
        return patch_ani(raw, values, new_length_arg)
    finally:
        scene.frame_set(keep[0], subframe=keep[1])
