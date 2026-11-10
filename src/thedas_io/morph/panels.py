# character preset panel: the parts a .mor brought in and its tint slots

from __future__ import annotations

import json
from typing import Any, ClassVar

import bpy

from .importer import preset_armature


class THEDAS_IO_OT_select_preset_part(bpy.types.Operator):
    bl_idname = "thedas_io.select_preset_part"
    bl_label = "Select preset part"
    bl_description = "Select the meshes of this part"
    index: bpy.props.IntProperty(default=0)  # type: ignore

    def execute(self, context: Any) -> set:
        armature = preset_armature(context)
        if armature is None:
            return {"CANCELLED"}
        parts = json.loads(armature.get("thedas_io_preset_parts") or "[]")
        if not 0 <= self.index < len(parts):
            return {"CANCELLED"}
        bpy.ops.object.select_all(action="DESELECT")
        for name in parts[self.index].get("objects") or ():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        return {"FINISHED"}


class THEDAS_IO_PT_preset(bpy.types.Panel):
    bl_label = "Character preset"
    bl_idname = "THEDAS_IO_PT_preset"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    @classmethod
    def poll(cls, context: Any) -> bool:
        return preset_armature(context) is not None

    def draw(self, context: Any) -> None:
        layout = self.layout
        if layout is None:
            return
        armature = preset_armature(context)
        if armature is None:
            return
        layout.label(text=str(armature.get("thedas_io_preset") or ""), icon="ARMATURE_DATA")

        parts = json.loads(armature.get("thedas_io_preset_parts") or "[]")
        box = layout.box()
        box.label(text=f"Parts ({len(parts)})")
        for part in parts:
            row = box.row(align=True)
            name = part.get("name") or "?"
            if not part.get("file"):
                row.label(text=name, icon="ERROR")
                continue
            op = row.operator("thedas_io.select_preset_part", text=name, icon="MESH_DATA")
            if op is not None:
                op.index = int(part.get("index") or 0)

        tints = json.loads(armature.get("thedas_io_preset_tints") or "[]")
        if tints:
            box = layout.box()
            box.label(text=f"Tint slots ({len(tints)})")
            for tint in tints:
                row = box.row()
                label = tint.get("name") or "?"
                if tint.get("file"):
                    row.label(text=label, icon="COLOR")
                else:
                    row.label(text=label, icon="ERROR")

        if "thedas_io_mop" in armature:
            box = layout.box()
            box.label(text=f"Preset override: {armature['thedas_io_mop']}", icon="MODIFIER")
