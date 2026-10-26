"""Animation import/export operators: play a .ani on the active armature."""

from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

import bpy

from ..assets import extract
from ..fsio import write_bytes_atomic


def _play_animation(context: Any, raw: bytes, name: str, fps: float, emit: Any) -> set:
    from .description import ANIReader
    from .scene import _fcurves, build_action

    arm = context.active_object
    if arm is None or arm.type != "ARMATURE":
        emit({"WARNING"}, "Select the armature that should play the animation")
        return {"CANCELLED"}

    try:
        anim = ANIReader(raw).animation
    except Exception as exc:  # noqa: BLE001 - a bad file must not crash
        emit({"ERROR"}, f"Could not read {name}: {exc}")
        return {"CANCELLED"}

    try:
        action = build_action(anim, arm, float(fps))
    except Exception as exc:  # noqa: BLE001
        emit({"ERROR"}, f"Could not build animation: {exc}")
        return {"CANCELLED"}

    curves = _fcurves(action)
    if not curves:
        emit({"WARNING"}, f"No bone in {arm.name} matched {anim.name}'s channels")
        return {"CANCELLED"}

    # the source travels with the action, so Export Animation can rewrite
    # the file's keys from the edited pose later
    action["thedas_io_ani_base64"] = base64.b64encode(raw).decode("ascii")
    action["thedas_io_ani_fps"] = float(fps)
    arm.animation_data_create().action = action

    scene = context.scene
    scene.render.fps = round(fps)
    scene.render.fps_base = 1.0
    scene.frame_start = 0
    scene.frame_end = max(1, math.ceil(anim.length * fps))
    scene.frame_set(0)

    emit({"INFO"}, f"{arm.name}: {len(curves)} curves from {anim.name}")
    return {"FINISHED"}


class THEDAS_IO_OT_load_animation(bpy.types.Operator):
    bl_idname = "thedas_io.load_animation"
    bl_label = "Load animation"
    bl_description = (
        "Play the selected .ani on the active armature: one Action of pose-bone "
        "quaternion and location keys. Reads the .ani from its archive the same "
        "way every asset is imported - a raw disk path is only used when given"
    )
    filepath: bpy.props.StringProperty(default="", subtype="FILE_PATH")  # type: ignore
    fps: bpy.props.FloatProperty(  # type: ignore
        default=30.0,
        min=1.0,
        name="Frames per second",
    )

    def execute(self, context: Any) -> set:
        arm = context.active_object
        if arm is None or arm.type != "ARMATURE":
            self.report({"WARNING"}, "Select the armature that should play the animation")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath or "")
        name = ""
        if path:
            if not Path(path).is_file():
                self.report({"WARNING"}, f"No such file: {path}")
                return {"CANCELLED"}
            name = Path(path).name
        else:
            # the standard asset route: the selected row came from a scan of
            # the .erf/.rim archives, extract pulls its bytes from that archive
            item = None
            try:
                item = context.scene.thedas_io_assets[context.scene.thedas_io_index]
            except IndexError:
                item = None
            if item is None:
                self.report(
                    {"WARNING"},
                    "Pick an animation in the asset list first, or give a raw .ani path",
                )
                return {"CANCELLED"}
            if str(item.ext).lower().lstrip(".") != "ani":
                self.report({"WARNING"}, f"{item.filename} is not an .ani animation")
                return {"CANCELLED"}
            row = {
                "package": item.package or None,
                "filename": item.filename,
                "ext": item.ext,
                "path": item.path,
                "name_hash": item.name_hash or None,
            }
            try:
                path = str(extract(row))
            except Exception as exc:  # noqa: BLE001
                self.report({"ERROR"}, f"Could not extract {item.filename}: {exc}")
                return {"CANCELLED"}
            name = item.filename
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            self.report({"ERROR"}, f"Could not read {name}: {exc}")
            return {"CANCELLED"}
        return _play_animation(context, raw, name, float(self.fps), self.report)


class THEDAS_IO_OT_export_animation(bpy.types.Operator):
    bl_idname = "thedas_io.export_animation"
    bl_label = "Export animation"
    bl_description = (
        "Write the active armature's action back to a .ani: edited keys are "
        "re-encoded, untouched channels keep their original bytes"
    )
    filepath: bpy.props.StringProperty(default="", subtype="FILE_PATH")  # type: ignore

    def invoke(self, context: Any, event: Any) -> set:
        action = self._action(context)
        if action is None:
            return {"CANCELLED"}
        self.filepath = f"{action.name}.ani"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _action(self, context: Any) -> Any:
        arm = context.active_object
        if arm is None or arm.type != "ARMATURE" or arm.animation_data is None:
            self.report({"WARNING"}, "Select the armature that plays the animation")
            return None
        action = arm.animation_data.action
        if action is None or "thedas_io_ani_base64" not in action:
            self.report(
                {"WARNING"}, "Its action was not imported from a .ani, so there is no source"
            )
            return None
        return action

    def execute(self, context: Any) -> set:
        from .export import export_action

        action = self._action(context)
        if action is None:
            return {"CANCELLED"}
        if not self.filepath:
            self.report({"WARNING"}, "No output path")
            return {"CANCELLED"}
        raw = base64.b64decode(action["thedas_io_ani_base64"])
        try:
            data = export_action(
                context, context.active_object, raw, float(action["thedas_io_ani_fps"])
            )
        except Exception as exc:  # noqa: BLE001 - report, do not crash Blender
            self.report({"ERROR"}, f"Could not export {action.name}: {exc}")
            return {"CANCELLED"}
        write_bytes_atomic(Path(self.filepath), data)
        self.report({"INFO"}, f"Wrote {self.filepath}")
        return {"FINISHED"}
