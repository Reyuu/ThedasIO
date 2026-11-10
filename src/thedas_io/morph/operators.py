# character presets

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from ..assets import extract
from .description import MOPReader, MORReader
from .importer import apply_mop, import_preset, preset_armature


def _finish(context: Any) -> None:
    # keep the asset/export panels in step with what just landed in the scene
    from ..importers import _show_materials, _store_registry, _sync_export_items

    _store_registry(context.scene)
    _sync_export_items(context.scene)
    _show_materials()


def run_preset(context: Any, raw: bytes, row: dict, emit: Any) -> set:
    # one path for the asset list, the file operator and both operators below.
    # a preset read from disk still resolves its parts from the game, so a da2
    # preset (whose parts are internal paths) works the same as one from an
    # archive; the preset's own folder is tried first either way.
    ext = str(row.get("ext") or "").lower().lstrip(".")
    if ext == "mop":
        preset = MOPReader(raw)
        armature = preset_armature(context)
        if armature is None:
            # a .mop carries its own parts: build the head it overrides first,
            # so picking a .mop from the asset list works on its own
            emit({"INFO"}, f"{row.get('filename')}: building the head it overrides")
            result = import_preset(context, preset, row, None, False, emit)
            if result != {"FINISHED"}:
                return result
            armature = preset_armature(context)
            if armature is None:
                return {"CANCELLED"}
        return apply_mop(context, preset, row, armature, None, False, emit)
    if ext != "mor":
        emit({"WARNING"}, f"No preset importer for {row.get('filename')}")
        return {"CANCELLED"}
    result = import_preset(context, MORReader(raw), row, None, False, emit)
    if result == {"FINISHED"}:
        _finish(context)
    return result


def _source(context: Any, filepath: str, ext: str, emit: Any) -> Any:
    # (raw bytes, row) from a raw path, else from the selected asset row
    path = bpy.path.abspath(filepath or "")
    if path:
        if not Path(path).is_file():
            emit({"WARNING"}, f"No such file: {path}")
            return None
        p = Path(path)
        return p.read_bytes(), {
            "package": None,
            "filename": p.name,
            "ext": p.suffix,
            "path": str(p),
            "name_hash": None,
        }
    scene = context.scene
    try:
        item = scene.thedas_io_assets[scene.thedas_io_index]
    except IndexError:
        item = None
    if item is None:
        emit({"WARNING"}, f"Pick a .{ext} in the asset list first, or give a raw path")
        return None
    if str(item.ext).lower().lstrip(".") != ext:
        emit({"WARNING"}, f"{item.filename} is not a .{ext} file")
        return None
    row = {
        "package": item.package or None,
        "filename": item.filename,
        "ext": item.ext,
        "path": item.path,
        "name_hash": item.name_hash or None,
    }
    try:
        return extract(row).read_bytes(), row
    except Exception as exc:  # noqa: BLE001 - a bad archive must not crash
        emit({"ERROR"}, f"Could not extract {item.filename}: {exc}")
        return None


class THEDAS_IO_OT_import_mor(bpy.types.Operator):
    bl_idname = "thedas_io.import_mor"
    bl_label = "Import character preset"
    bl_description = (
        "Import a .mor: every part it lists, as one character on the first "
        "part's rig, with its tint slots and material values"
    )
    filepath: bpy.props.StringProperty(default="", subtype="FILE_PATH")  # type: ignore

    def execute(self, context: Any) -> set:
        source = _source(context, self.filepath, "mor", self.report)
        if source is None:
            return {"CANCELLED"}
        raw, row = source
        try:
            return run_preset(context, raw, row, self.report)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not read {row['filename']}: {exc}")
            return {"CANCELLED"}


class THEDAS_IO_OT_import_mop(bpy.types.Operator):
    bl_idname = "thedas_io.import_mop"
    bl_label = "Apply morph preset"
    bl_description = (
        "Apply a .mop to the imported preset character: its colour rows go on "
        "the matching material inputs, the rest is kept on the rig"
    )
    filepath: bpy.props.StringProperty(default="", subtype="FILE_PATH")  # type: ignore

    def execute(self, context: Any) -> set:
        if preset_armature(context) is None:
            self.report({"WARNING"}, "Import a character preset first")
            return {"CANCELLED"}
        source = _source(context, self.filepath, "mop", self.report)
        if source is None:
            return {"CANCELLED"}
        raw, row = source
        try:
            return run_preset(context, raw, row, self.report)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not read {row['filename']}: {exc}")
            return {"CANCELLED"}
