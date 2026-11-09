# collision panel: the model's shapes, with the add/move/remove buttons.

from __future__ import annotations

from typing import Any, ClassVar

import bpy

from ..export import phy_enabled
from .operators import current_item

KINDS = (("BOX", "Box"), ("SPHERE", "Sphere"), ("CAPSULE", "Capsule"), ("MESH", "Mesh"))

# what the file calls each shape, for the row label
_LABEL = {
    "boxs": "box",
    "box": "box",
    "sphs": "sphere",
    "sphere": "sphere",
    "caps": "capsule",
    "capsule": "capsule",
    "mshs": "mesh",
    "mesh": "mesh",
}


class THEDAS_IO_PT_collision(bpy.types.Panel):
    bl_label = "Collision"
    bl_idname = "THEDAS_IO_PT_collision"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    @classmethod
    def poll(cls, context: Any) -> bool:
        return phy_enabled(context)

    def draw(self: Any, context: Any) -> None:
        layout = self.layout
        if layout is None:
            return
        item = current_item(context)
        if item is None:
            layout.label(text="Import a model with a .phy to edit its collision")
            return
        arm = item.get("armature")

        row = layout.row(align=True)
        for kind, label in KINDS:
            op = row.operator("thedas_io.add_collision", text=f"Add {label}")
            if op is not None:
                op.kind = kind
        if arm is not None and hasattr(arm, "data"):
            layout.prop_search(
                context.scene, "thedas_io_collision_bone", arm.data, "bones", text="New shape bone"
            )

        proxies = list(item.get("collision") or [])
        layout.label(text=f"{len(proxies)} shape(s); select one for its handles")
        for obj in proxies:
            kind = _LABEL.get(str(obj.get("thedas_io_phy_kind") or ""), "shape")
            row = layout.row(align=True)
            row.label(text=f"{obj.name} ({kind})")
            op = row.operator(
                "thedas_io.pick_collision_bone",
                text=str(obj.get("thedas_io_phy_bone") or "no bone"),
            )
            if op is not None:
                op.shape = obj.name
            op = row.operator("thedas_io.remove_collision", text="Remove")
            if op is not None:
                op.shape = obj.name
