"""Bone-order lookup and the vertex-group bone mapping UI."""

from __future__ import annotations

from typing import Any, ClassVar

import bpy

BONE_INDEX = "thedas_io_bone_index"
BONE_ORDER = "thedas_io_bone_order"
BONE_GLOBAL = "thedas_io_bone_global"
NODE_TAG = "thedas_io_node"
_UNMAPPED = "__thedas_unmapped__"


def _ints(values: Any) -> list[int]:
    try:
        return [int(v) for v in values or [] if isinstance(v, int)]
    except (TypeError, ValueError):
        return []


def order_of(obj: Any, arm: Any = None) -> list[int]:
    # the mesh's own order wins; the armature's only serves hand-built meshes
    for source in (obj, arm):
        if source is None:
            continue
        try:
            order = _ints(source.get(BONE_ORDER))
        except (AttributeError, ReferenceError, TypeError):
            continue
        if order:
            return order
    return []


def armature_of(obj: Any) -> Any:
    try:
        mods = list(obj.modifiers)
    except (AttributeError, ReferenceError):
        return None
    return next(
        (m.object for m in mods if getattr(m, "type", "") == "ARMATURE" and m.object is not None),
        None,
    )


def local_for_global(order: list[int], global_id: Any) -> int | None:
    try:
        return order.index(int(global_id))
    except (TypeError, ValueError):
        return None


def global_for_bone(bone: Any) -> int | None:
    try:
        value = bone.get(BONE_GLOBAL)
    except (AttributeError, ReferenceError):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def index_for_bone(obj: Any, arm: Any, bone_name: str) -> int | None:
    if arm is None:
        arm = armature_of(obj)
    if arm is None:
        return None
    try:
        bone = arm.pose.bones.get(bone_name)
    except (AttributeError, ReferenceError):
        return None
    return None if bone is None else local_for_global(order_of(obj, arm), global_for_bone(bone))


def index_for_group(obj: Any, group_name: str) -> int | None:
    return index_for_bone(obj, armature_of(obj), group_name)


def bone_for_index(obj: Any, arm: Any, local: int) -> str | None:
    order = order_of(obj, arm)
    if local < 0 or local >= len(order) or arm is None:
        return None
    global_id = order[local]
    try:
        bones = list(arm.pose.bones)
    except (AttributeError, ReferenceError):
        return None
    for bone in bones:
        if global_for_bone(bone) == global_id:
            return str(bone.name)
    return None


def bone_labels(obj: Any) -> dict[str, int]:
    # bone name -> local index for every bone the mesh's order can address
    arm = armature_of(obj)
    if arm is None:
        return {}
    order = order_of(obj, arm)
    out: dict[str, int] = {}
    if not order:
        return out
    try:
        bones = list(arm.pose.bones)
    except (AttributeError, ReferenceError):
        return out
    for bone in bones:
        local = local_for_global(order, global_for_bone(bone))
        if local is not None:
            out.setdefault(str(bone.name), local)
    return out


def bone_entries(obj: Any) -> list[tuple[str, int | None]]:
    # every pose bone, with None when the mesh's order cannot address it
    arm = armature_of(obj)
    if arm is None:
        return []
    labels = bone_labels(obj)
    try:
        bones = list(arm.pose.bones)
    except (AttributeError, ReferenceError):
        return []
    return [(str(bone.name), labels.get(str(bone.name))) for bone in bones]


def current_index(obj: Any, group_name: str) -> int | None:
    try:
        mapping = dict(obj.get(BONE_INDEX) or {})
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return None
    value = mapping.get(group_name)
    return value if isinstance(value, int) else None


def set_bone_index(obj: Any, group_name: str, local: int | None) -> None:
    try:
        mapping = dict(obj.get(BONE_INDEX) or {})
    except (AttributeError, ReferenceError, TypeError, ValueError):
        mapping = {}
    if local is None:
        mapping.pop(group_name, None)
    else:
        mapping[group_name] = int(local)
    obj[BONE_INDEX] = mapping


def _allow_unindexed(context: Any) -> bool:
    scene = getattr(context, "scene", None)
    return bool(getattr(scene, "thedas_io_allow_unindexed_bones", False))


def _on_search(self: Any, context: Any) -> None:
    # a filtered-out selection is invalid; reset before the enum is rebuilt
    self.bone = _UNMAPPED
    if getattr(context, "area", None) is not None:
        context.area.tag_redraw()


def _bone_items(_self: Any, context: Any) -> list:
    obj = getattr(context, "object", None)
    items = [(_UNMAPPED, "Unmapped", "Remove this group's bone mapping")]
    if obj is None or getattr(obj, "type", "") != "MESH":
        return items
    query = str(getattr(_self, "search", "") or "").strip().lower()
    allow = _allow_unindexed(context)
    for name, local in bone_entries(obj):
        if query and query not in name.lower():
            continue
        if local is None:
            if allow:
                items.append((name, f"{name}  (no index)", "Not in this mesh's bone order"))
            continue
        items.append((name, f"{name}  (#{local})", f"Bone index {local}"))
    return items


class THEDAS_IO_OT_pick_group_bone(bpy.types.Operator):
    bl_idname = "thedas_io.pick_group_bone"
    bl_label = "Map Vertex Group to Bone"
    bl_description = "Save this vertex group as a bone index in the exported .msh"
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}  # type: ignore
    group: bpy.props.StringProperty(name="Vertex group")  # type: ignore
    search: bpy.props.StringProperty(  # type: ignore
        name="Search",
        description="Filter the bone list by name",
        update=_on_search,
    )
    bone: bpy.props.EnumProperty(  # type: ignore
        name="Bone",
        items=_bone_items,
    )
    index: bpy.props.IntProperty(  # type: ignore
        name="Bone index",
        description="Advanced: raw MSH bone index; -1 uses the picked bone",
        default=-1,
        min=-1,
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = getattr(context, "object", None)
        return obj is not None and getattr(obj, "type", "") == "MESH"

    def invoke(self, context: Any, event: Any) -> set:
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context: Any) -> None:
        layout = self.layout
        if layout is None:
            return
        layout.label(text=f"Vertex group: {self.group}")
        if not bone_labels(context.object):
            layout.label(text="No armature bone order on this mesh")
        allow = _allow_unindexed(context)
        layout.prop(self, "search")
        layout.prop(self, "bone")
        if allow:
            layout.prop(self, "index")
            if (
                self.bone != _UNMAPPED
                and self.index < 0
                and self.bone not in bone_labels(context.object)
            ):
                layout.label(text="This bone has no stored MSH index; set one manually.")

    def execute(self, context: Any) -> set:
        obj = getattr(context, "object", None)
        if obj is None:
            return {"CANCELLED"}
        allow = _allow_unindexed(context)
        if allow and self.index >= 0:
            set_bone_index(obj, self.group, self.index)
            return {"FINISHED"}
        if self.bone == _UNMAPPED:
            set_bone_index(obj, self.group, None)
            return {"FINISHED"}
        local = index_for_bone(obj, armature_of(obj), self.bone)
        if local is None:
            message = (
                f"{self.bone!r} has no MSH index; set Bone index manually"
                if allow
                else f"{self.bone!r} is not in this mesh's bone order"
            )
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        set_bone_index(obj, self.group, local)
        return {"FINISHED"}


class THEDAS_IO_OT_auto_map_bones(bpy.types.Operator):
    bl_idname = "thedas_io.auto_map_bones"
    bl_label = "Auto-map same-named groups"
    bl_description = (
        "Map every vertex group whose name exactly matches a bone in this mesh's skeleton"
    )
    bl_options: ClassVar[set[str]] = {"REGISTER", "UNDO"}  # type: ignore

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = getattr(context, "object", None)
        return obj is not None and getattr(obj, "type", "") == "MESH"

    def execute(self, context: Any) -> set:
        obj = getattr(context, "object", None)
        if obj is None:
            return {"CANCELLED"}
        labels = bone_labels(obj)
        count = 0
        for group in obj.vertex_groups:
            local = labels.get(group.name)
            if local is not None and current_index(obj, group.name) != local:
                set_bone_index(obj, group.name, local)
                count += 1
        self.report({"INFO"}, f"Mapped {count} group(s)")
        return {"FINISHED"}


class THEDAS_IO_PT_bone_map(bpy.types.Panel):
    bl_label = "ThedasIO Bone Map"
    bl_idname = "THEDAS_IO_PT_bone_map"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = getattr(context, "object", None)
        return obj is not None and getattr(obj, "type", "") == "MESH"

    def draw(self, context: Any) -> None:
        layout = self.layout
        if layout is None:
            return
        obj = context.object
        arm = armature_of(obj)
        if arm is None:
            layout.label(text="No armature modifier. Add the model's armature to map groups.")
            return
        if not order_of(obj, arm):
            layout.label(
                text="Bone order unknown. Re-import this mesh with its .mmh, then map groups."
            )
            return
        if arm.get("thedas_io_bone_order_mixed"):
            layout.label(text="This rig mixes bone orders. Map groups on the imported mesh.")
        layout.prop(
            context.scene,
            "thedas_io_allow_unindexed_bones",
            text="Allow bone index assignment for unindexed bones",
        )
        labels = bone_labels(obj)
        total = len(arm.pose.bones)
        if len(labels) < total:
            if _allow_unindexed(context):
                layout.label(text=f"{total - len(labels)} bone(s) need a manual MSH index")
            else:
                layout.label(
                    text=f"{total - len(labels)} bone(s) are hidden; enable the checkbox above"
                )

        layout.label(text="Each group is saved as a bone index in the .msh on export.")
        layout.label(text="Pick a bone per group. Auto-map matches groups with the same bone name.")
        layout.operator("thedas_io.auto_map_bones", text="Auto-map same-named groups")
        missing = 0
        for group in obj.vertex_groups:
            local = current_index(obj, group.name)
            row = layout.row(align=True)
            row.label(text=group.name)
            if local is None:
                missing += 1
                op = row.operator("thedas_io.pick_group_bone", text="Pick bone...")
            else:
                name = bone_for_index(obj, arm, local) or "unknown bone"
                op = row.operator("thedas_io.pick_group_bone", text=f"{name} (#{local})")
            if op is not None:
                op.group = group.name
        if missing:
            layout.label(text=f"{missing} group(s) still need a bone")
