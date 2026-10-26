"""Collision editing operators and the .phy import helper."""

from __future__ import annotations

import struct

import bpy

from ..assets import resolve
from ..export import ITEMS, phy_enabled
from .description import PhyReader
from .scene import add_proxy_for, build_proxies, default_name, mark_snapshots


class THEDAS_IO_OT_add_collision(bpy.types.Operator):
    bl_idname = "thedas_io.add_collision"
    bl_label = "Add collision"
    bl_description = (
        "Add a collision primitive to the selected bone and write it into the "
        "model's .phy on export"
    )
    kind: bpy.props.EnumProperty(  # type: ignore
        name="Kind",
        items=(
            ("CAPSULE", "Capsule", ""),
            ("SPHERE", "Sphere", ""),
            ("BOX", "Box", ""),
            ("MESH", "Mesh", "Box-shaped mesh collision"),
        ),
        default="CAPSULE",
    )
    radius: bpy.props.FloatProperty(name="Radius", default=0.1, min=0.0001)  # type: ignore
    height: bpy.props.FloatProperty(name="Height", default=0.2, min=0.0)  # type: ignore
    _timer = None
    _state = None
    _db_path = ""

    @classmethod
    def poll(cls, context) -> bool:

        return phy_enabled(context)

    def _target(self, context):
        # the model whose .phy gets the new shape: prefer the armature in
        # context, else the one item that was imported
        arm = context.active_object
        if arm is None or arm.type != "ARMATURE":
            arms = [o for o in context.scene.objects if o.type == "ARMATURE"]
            arm = arms[-1] if arms else None
        if arm is None:
            return None, None
        # an explicit choice from the panel always wins over the selection
        chosen = context.scene.thedas_io_collision_bone
        if chosen and chosen in arm.data.bones:
            return arm, arm.data.bones[chosen]
        bone = None
        if context.active_object is arm and arm.mode == "EDIT":
            bone = arm.data.edit_bones.active
        else:
            # PoseBone.select, not bone_select: Blender 5.2 renamed it and the
            # old attribute is gone
            active = next((p.bone for p in arm.pose.bones if p.select), None)
            bone = active or (arm.data.bones[0] if len(arm.data.bones) else None)
        return arm, bone

    def invoke(self, context, event) -> set:

        if not phy_enabled(context):
            self.report(
                {"INFO"},
                "Enable 'experimental PHY support' in addon settings before adding collision",
            )
            return {"CANCELLED"}
        arm, bone = self._target(context)
        if arm is None:
            self.report({"WARNING"}, "Import a model first: no armature in the scene")
            return {"CANCELLED"}
        if bone is None:
            self.report({"WARNING"}, "This model has no bones to attach collision to")
            return {"CANCELLED"}
        item = _collision_item(arm)
        if item is None or not item.get("phy_raw"):
            self.report({"WARNING"}, "This model has no .phy to add collision to")
            return {"CANCELLED"}
        if self.kind == "MESH":
            # the NXS tail (adjacency + OPC/HBM acceleration sections) is not
            # decoded, so a new mesh cannot be written without guessing it
            self.report(
                {"WARNING"},
                "Mesh collision can be edited, not created yet: its "
                "acceleration sections are not decoded. Pick Capsule, "
                "Sphere or Box",
            )
            return {"CANCELLED"}

        spec = {
            "bone": bone.name,
            "kind": self.kind.lower(),
            "name": default_name(self.kind.lower(), bone.name),
            "radius": float(self.radius),
            "height": float(self.height),
            "box_dim": (self.radius * 2, self.radius * 2, self.height),
            "position": (0.0, 0.0, 0.0),
            "rotation": (0.0, 0.0, 0.0, 1.0),
        }
        item.setdefault("phy_additions", []).append(spec)
        added = _spawn_added_proxy(context, item, spec)
        self.report(
            {"INFO"}, f"Added {spec['kind']} to {bone.name}" + (f" ({added})" if added else "")
        )
        return {"FINISHED"}

    def execute(self, context) -> set:
        return self.invoke(context, None)


def _collision_item(arm):

    for item in ITEMS.values():
        if item.get("kind") != "mmh" or not item.get("phy_raw"):
            continue
        if item.get("armature") is arm:
            return item
    return None


def _spawn_added_proxy(context, item, spec) -> str:
    """give the new shape a proxy straight away, at the bone it was added to"""
    arm = item.get("armature")
    bone = arm.data.bones.get(spec["bone"]) if arm is not None else None
    if bone is None:
        return ""

    obj = add_proxy_for(context, spec, arm, bone)
    item.setdefault("collision", []).append(obj)
    mark_snapshots([obj])
    return obj.name


def _load_collision(context, row, stem, reg, arm_obj, db, local_only, emit) -> list:
    # a .phy beside the .mmh becomes plain proxy objects on the bones it names,
    # only while experimental PHY support is switched on

    if not phy_enabled():
        return []
    hit = resolve(f"{stem}.phy", row, db, local_only)
    if hit is None or not hit.is_file():
        return []
    try:
        reader = PhyReader(hit.read_bytes())
    except (ValueError, struct.error, OSError) as exc:
        emit({"WARNING"}, f"{hit.name}: could not read collision ({exc})")
        return []
    if not reader.shapes:
        return []

    bones: dict = {}
    if arm_obj is not None:
        bones = {b.name: arm_obj for b in arm_obj.data.bones}
    proxies = build_proxies(context, reader, bones)
    mark_snapshots(proxies)
    reg["collision"] = proxies
    reg["phy_raw"] = hit.read_bytes()
    reg["phy_name"] = hit.name
    return proxies
