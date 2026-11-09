"""Collision editing operators and the .phy import helper."""

from __future__ import annotations

import struct
from typing import Any, ClassVar

import bpy

from ..assets import resolve
from ..export import ITEMS, phy_enabled
from .description import PhyReader
from .scene import (
    _bone_parent_inverse,
    _path_of,
    _values,
    add_proxy_for,
    build_proxies,
    default_name,
    mark_snapshots,
    node_transform,
    object_geometry,
    object_hull,
)


def _bone_items(_self: Any, context: Any) -> list:
    # every bone of the armature the shape being moved lives on
    obj = bpy.data.objects.get(str(getattr(_self, "shape", "") or ""))
    arm = getattr(obj, "parent", None) if obj is not None else None
    if arm is None or getattr(arm, "type", "") != "ARMATURE":
        arms = [
            o
            for o in getattr(getattr(context, "scene", None), "objects", [])
            if o.type == "ARMATURE"
        ]
        arm = arms[-1] if arms else None
    if arm is None:
        return []
    return [(b.name, b.name, "Hang the shape off this bone") for b in arm.data.bones]


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
    def poll(cls, context: Any) -> bool:

        return phy_enabled(context)

    def _target(self, context: Any) -> Any:
        # the model whose .phy gets the new shape: the selected object's
        # armature, else the last imported one
        arm = context.active_object
        if arm is not None and arm.type != "ARMATURE":
            parent = arm.parent
            while parent is not None and parent.type != "ARMATURE":
                parent = parent.parent
            arm = parent
        if arm is None:
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

    def invoke(self, context: Any, event: Any) -> set:

        if not phy_enabled(context):
            self.report(
                {"INFO"},
                "Enable 'PHY collision' in addon settings before adding collision",
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
            source = context.active_object
            if source is None or getattr(source, "type", "") != "MESH":
                self.report({"WARNING"}, "Select the model mesh to build collision from")
                return {"CANCELLED"}
            mesh = source.data
            if any(p.select for p in mesh.polygons):
                verts, tris = object_geometry(source, selected_only=True)
            elif any(v.select for v in mesh.vertices):
                verts, tris = object_hull(source)
            else:
                verts, tris = object_geometry(source)
            if not tris:
                self.report({"WARNING"}, f"{source.name}: no faces to build collision from")
                return {"CANCELLED"}
            position, rotation, scale = node_transform(source, arm, bone)
            spec = {
                "bone": bone.name,
                "kind": "mesh",
                "name": default_name("mesh", bone.name),
                "position": position,
                "rotation": rotation,
                "verts": [(v[0] * scale[0], v[1] * scale[1], v[2] * scale[2]) for v in verts],
                "tris": tris,
            }
            from .cooker import CookerError, cook_in_context

            try:
                cook_in_context(spec["verts"], spec["tris"], context)
            except (CookerError, OSError, ValueError) as exc:
                self.report({"WARNING"}, f"Mesh collision could not be cooked: {exc}")
                return {"CANCELLED"}
        else:
            spec = {
                "bone": bone.name,
                "kind": self.kind.lower(),
                "name": default_name(self.kind.lower(), bone.name),
                "radius": float(self.radius),
                "height": float(self.height),
                "box_dim": (self.radius * 2, self.radius * 2, self.height),
                "position": _bone_local(arm, bone, context.scene.cursor.location),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            }
        item.setdefault("phy_additions", []).append(spec)
        added = _spawn_added_proxy(context, item, spec)
        self.report(
            {"INFO"}, f"Added {spec['kind']} to {bone.name}" + (f" ({added})" if added else "")
        )
        return {"FINISHED"}

    def execute(self, context: Any) -> set:
        return self.invoke(context, None)


def _collision_item(arm: Any) -> Any:

    for item in ITEMS.values():
        if item.get("kind") != "mmh" or not item.get("phy_raw"):
            continue
        if item.get("armature") is arm:
            return item
    return None


def _item_for_proxy(obj: Any) -> Any:
    # the .phy that owns this proxy, via the owner tag build_proxies wrote
    owner = str(obj.get("thedas_io_phy_owner") or "")
    item = ITEMS.get(owner)
    if item is not None and item.get("phy_raw"):
        return item
    return _collision_item(getattr(obj, "parent", None))


def _bone_local(arm: Any, bone: Any, world: Any) -> tuple:
    # root bones take model-space positions; child bones take node-space offsets
    from mathutils import Vector

    local = arm.matrix_world.inverted() @ world
    if bone.parent is None or str(bone.name).lower() == "root":
        head = bone.head_local
        return (local[0] - head[0], local[1] - head[1], local[2] - head[2])
    offset = bone.matrix_local.to_quaternion().inverted() @ (Vector(local) - bone.head_local)
    return (offset[1], offset[2], offset[0])


def _active_proxy(context: Any) -> Any:
    obj = getattr(context, "active_object", None)
    if obj is not None and obj.get("thedas_io_phy_kind"):
        return obj
    return None


def current_item(context: Any) -> Any:
    # the model the UI is about: whatever the selection belongs to, else last imported
    item = _item_for_object(getattr(context, "active_object", None))
    if item is not None:
        return item
    for item in reversed(list(ITEMS.values())):
        if item.get("kind") == "mmh" and item.get("phy_raw"):
            return item
    return None


def _item_for_object(obj: Any) -> Any:
    if obj is None:
        return None
    owner = str(obj.get("thedas_io_phy_owner") or "")
    item = ITEMS.get(owner)
    if item is not None and item.get("phy_raw"):
        return item
    if obj.get("thedas_io_phy_kind"):
        return _item_for_proxy(obj)
    # walk up: the armature, one of its meshes, or anything parented to them
    walk = obj
    while walk is not None:
        for item in ITEMS.values():
            if item.get("kind") != "mmh" or not item.get("phy_raw"):
                continue
            if item.get("armature") is walk or walk in (item.get("meshes") or []):
                return item
        walk = getattr(walk, "parent", None)
    return None


class THEDAS_IO_OT_remove_collision(bpy.types.Operator):
    bl_idname = "thedas_io.remove_collision"
    bl_label = "Remove collision"
    bl_description = (
        "Remove this collision shape: a shape added in Blender is dropped, an "
        "imported one is cut out of the model's .phy on export"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}  # type: ignore
    shape: bpy.props.StringProperty(  # type: ignore
        name="Shape", description="Name of the collision proxy to remove"
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return phy_enabled(context)

    def execute(self, context: Any) -> set:
        obj = _active_proxy(context) if not self.shape else bpy.data.objects.get(self.shape)
        if obj is None or not obj.get("thedas_io_phy_kind"):
            self.report({"WARNING"}, "Select a collision shape to remove")
            return {"CANCELLED"}
        item = _item_for_proxy(obj)
        if item is None:
            self.report({"WARNING"}, "This shape has no .phy to remove it from")
            return {"CANCELLED"}

        name = str(obj.get("thedas_io_phy_name") or "")
        path = _path_of(obj)
        if obj.get("thedas_io_phy_new"):
            _drop_addition(item, name)
        elif path:
            # in the file: export cuts its instance out of the parent's CHILDREN list
            item.setdefault("phy_removals", []).append(list(path))
        else:
            self.report({"WARNING"}, f"{obj.name}: no place in the .phy to remove")
            return {"CANCELLED"}

        proxies = item.get("collision") or []
        item["collision"] = [p for p in proxies if p != obj]
        item["phy_shapes"] = [s for s in (item.get("phy_shapes") or []) if tuple(s.path) != path]
        label = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        self.report({"INFO"}, f"Removed {label}")
        return {"FINISHED"}


def _drop_addition(item: Any, name: str) -> None:
    additions = list(item.get("phy_additions") or [])
    for index, spec in enumerate(additions):
        if str(spec.get("name") or "") == name:
            del additions[index]
            break
    item["phy_additions"] = additions


class THEDAS_IO_OT_pick_collision_bone(bpy.types.Operator):
    bl_idname = "thedas_io.pick_collision_bone"
    bl_label = "Move Collision to Bone"
    bl_description = (
        "Hang this collision shape off another bone: the shape is written under "
        "the picked bone's collision object on export"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}  # type: ignore
    shape: bpy.props.StringProperty(name="Shape")  # type: ignore
    bone: bpy.props.EnumProperty(name="Bone", items=_bone_items)  # type: ignore

    @classmethod
    def poll(cls, context: Any) -> bool:
        return phy_enabled(context)

    def invoke(self, context: Any, event: Any) -> set:
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context: Any) -> None:
        if self.layout is not None:
            self.layout.label(text=f"Shape: {self.shape}")

    def execute(self, context: Any) -> set:
        obj = bpy.data.objects.get(self.shape)
        if obj is None or not self.bone:
            return {"CANCELLED"}
        item = _item_for_proxy(obj)
        arm = item.get("armature") if item else None
        bone = arm.data.bones.get(self.bone) if arm is not None else None
        if item is None or bone is None:
            self.report({"WARNING"}, f"{self.bone}: no such bone on this model")
            return {"CANCELLED"}

        if obj.get("thedas_io_phy_new"):
            for spec in item.get("phy_additions") or []:
                if str(spec.get("name") or "") == str(obj.get("thedas_io_phy_name") or ""):
                    spec["bone"] = bone.name
                    break
        else:
            # moving a file shape = cut it out of the old bone, add it under the new
            path = _path_of(obj)
            if not path:
                self.report({"WARNING"}, f"{obj.name}: no .phy entry to move")
                return {"CANCELLED"}
            spec = _shape_spec(obj, item)
            spec["bone"] = bone.name
            item.setdefault("phy_removals", []).append(list(path))
            item.setdefault("phy_additions", []).append(spec)

        obj.parent = arm
        obj.parent_type = "BONE"
        obj.parent_bone = bone.name
        obj.matrix_parent_inverse = _bone_parent_inverse(bone)
        obj["thedas_io_phy_bone"] = bone.name
        self.report({"INFO"}, f"{obj.name} -> {bone.name}")
        return {"FINISHED"}


def _shape_spec(obj: Any, item: Any) -> dict:
    # the shape as the file holds it, in the shape add_shapes takes
    values = _values(obj)
    kind = _primitive_kind(obj.get("thedas_io_phy_kind"))
    spec: dict = {
        "bone": obj.get("thedas_io_phy_bone") or "",
        "kind": kind,
        "name": values.get("name") or "",
        "position": tuple(values.get("position") or (0.0, 0.0, 0.0))[:3],
        "rotation": tuple(values.get("rotation") or (0.0, 0.0, 0.0, 1.0)),
        "masks": {},
    }
    if kind == "mesh":
        from .scene import object_geometry

        verts, tris = object_geometry(obj)
        spec["verts"] = verts
        spec["tris"] = tris
    if "radius" in values:
        spec["radius"] = float(values["radius"])
    if "height" in values:
        spec["height"] = float(values["height"])
    if "box_dim" in values:
        spec["box_dim"] = tuple(float(v) for v in values["box_dim"])
    for key, value in values.items():
        if key.startswith("mask_"):
            spec["masks"][key[len("mask_") :]] = int(value)
    return spec


def _primitive_kind(kind: Any) -> str:
    return {
        "caps": "capsule",
        "sphs": "sphere",
        "boxs": "box",
        "mshs": "mesh",
        "mesh": "mesh",
    }.get(str(kind or ""), "capsule")


def _spawn_added_proxy(context: Any, item: Any, spec: dict) -> str:
    """give the new shape a proxy straight away, at the bone it was added to"""
    arm = item.get("armature")
    bone = arm.data.bones.get(spec["bone"]) if arm is not None else None
    if bone is None:
        return ""

    owner = item.get("file", "")
    obj = add_proxy_for(context, spec, arm, bone, owner)
    item.setdefault("collision", []).append(obj)
    mark_snapshots([obj])
    return obj.name


def _load_collision(
    context: Any,
    row: dict,
    stem: str,
    reg: Any,
    arm_obj: Any,
    db: Any,
    local_only: bool,
    emit: Any,
) -> list:
    # a .phy beside the .mmh becomes plain proxy objects on the bones it names,
    # unless the switch is off

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
    owner = reg.get("file", "")
    proxies = build_proxies(context, reader, bones, owner)
    mark_snapshots(proxies)
    # ensure all proxies have the owner tag
    for p in proxies:
        if "thedas_io_phy_owner" not in p:
            p["thedas_io_phy_owner"] = owner
    reg["collision"] = proxies
    # the parsed shapes behind the proxies: the data model the proxies are a
    # view of, so gizmos and tests can read the file's own values
    reg["phy_shapes"] = reader.shapes
    reg["phy_raw"] = hit.read_bytes()
    reg["phy_name"] = hit.name
    return proxies
