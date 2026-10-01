# blender ui panels, lists and operators

import io
import json
import math
import shutil
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any, ClassVar

import bpy
import numpy as np
from mathutils import Matrix, Quaternion
from PIL import Image as PilImage

from .asset_db.scanner import find, scan
from .assets import extract, importer_for, resolve
from .export import (
    _LOADED_ITEMS,
    ITEMS,
    _tnt_name,
    check_model,
    connected,
    export_all,
    remember,
    reset,
    roots,
)
from .geometry import bone_names, decode_chunk, flatten, node_paths, quat_rotate, weld_positions
from .materials import parse_mao, role_of, slot_roles, thedas_io_normals_to_rgba
from .mmh.description import MMHReader
from .msh.description import MSHReader
from .substance import (
    MAP_NORMAL,
    attribute_bare,
    collect_sp_maps,
    mtl_text,
    obj_text,
    repack_images,
    unpack_images,
    unpack_plan,
)
from .tnt import DEFAULTS, alpha_input, for_side, group_name, parse


class THEDAS_IO_AssetItem(bpy.types.PropertyGroup):
    package: bpy.props.StringProperty()  # type: ignore
    filename: bpy.props.StringProperty()  # type: ignore
    ext: bpy.props.StringProperty()  # type: ignore
    path: bpy.props.StringProperty()  # type: ignore


def _viewport_objects(item: dict) -> list:
    # viewport objects owned by one imported file
    kind = item.get("kind")
    if kind == "msh":
        objs = item.get("objects") or []
        return [e[0] if isinstance(e, (tuple, list)) else e for e in objs]
    if kind == "mmh":
        objs = list(item.get("meshes") or [])
        for entry in (item.get("hosts") or {}).values():
            try:
                entry_kind, resolver = entry[0], entry[1]
            except (TypeError, IndexError):
                continue
            if entry_kind == "object":
                objs.append(resolver)

        if item.get("armature") is not None:
            objs.append(item["armature"])
        return objs
    return []  # materials and images have no viewport presence


def _set_tree_visible(root_key: str, show: bool) -> None:
    # show/hide one hierarchy root, blend data untouched

    for key in connected({root_key}):
        item = ITEMS.get(key)
        if item is None:
            continue
        for obj in _viewport_objects(item):
            try:
                obj.hide_viewport = not show
            except (AttributeError, ReferenceError):
                continue


def _on_use_toggled(self, context) -> None:
    # object list checkbox, hides tree but keeps it in blend
    _set_tree_visible(self.filename, self.use)
    _sync_rename_items(context.scene)


class THEDAS_IO_ExportItem(bpy.types.PropertyGroup):
    # one row per hierarchy root, unchecked hides tree and skips export
    filename: bpy.props.StringProperty()  # type: ignore
    use: bpy.props.BoolProperty(default=True, update=_on_use_toggled)  # type: ignore


class THEDAS_IO_RenameItem(bpy.types.PropertyGroup):
    # one row per exported file, objects untouched
    filename: bpy.props.StringProperty()  # type: ignore
    export_name: bpy.props.StringProperty(  # type: ignore
        name="Export as",
        description="Output file name for this resource; blank keeps the source "
        "name. Rename related files together: a renamed hierarchy "
        "repoints its internal references on export",
    )


class THEDAS_IO_UL_export(bpy.types.UIList):
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index, flt_flag=None
    ):
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=item.filename)
            return
        split = layout.split(factor=0.12, align=True)
        split.prop(item, "use", text="")
        split.label(text=item.filename)

    @classmethod
    def draw_header(cls, layout):
        split = layout.split(factor=0.12, align=True)
        split.label(text="Show")
        split.label(text="File")


class THEDAS_IO_UL_rename(bpy.types.UIList):
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index, flt_flag=None
    ):
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=item.filename)
            return
        split = layout.split(factor=0.5, align=True)
        split.label(text=item.filename)
        split.prop(item, "export_name", text="")

    @classmethod
    def draw_header(cls, layout):
        split = layout.split(factor=0.5, align=True)
        split.label(text="File")
        split.label(text="Export as")


class THEDAS_IO_UL_assets(bpy.types.UIList):
    # filename, extension, package columns, header drawn by panel
    COLUMNS = (0.55, 0.15, 0.30)

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index, flt_flag=None
    ):
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=f"{item.filename}  ({item.package or '—'})")
            return
        name, ext = item.filename, item.ext or ""
        if ext and name.lower().endswith(ext.lower()):
            name = name[: -len(ext)]

        split = layout.split(factor=self.COLUMNS[0], align=True)
        split.label(text=name)
        split = split.split(factor=self.COLUMNS[1] / (1.0 - self.COLUMNS[0]), align=True)
        split.label(text=ext.lstrip(".") or "—")
        split.label(text=item.package or "—")

    @classmethod
    def draw_header(cls, layout):
        split = layout.split(factor=cls.COLUMNS[0], align=True)
        split.label(text="File")
        split = split.split(factor=cls.COLUMNS[1] / (1.0 - cls.COLUMNS[0]), align=True)
        split.label(text="Type")
        split.label(text="Package")


class THEDAS_IO_PT_assets(bpy.types.Panel):
    bl_label = "ThedasIO Assets"
    bl_idname = "THEDAS_IO_PT_assets"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"

    def draw(self, context):
        pass


class THEDAS_IO_PT_import(bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "THEDAS_IO_PT_import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    def draw(self, context):
        scene = context.scene
        if self.layout is None:
            return
        self.layout.prop(scene, "thedas_io_import_source", expand=True)
        if scene.thedas_io_import_source == "GAME":
            self.layout.prop(scene, "thedas_io_filter_ext", text="Extension")
            self.layout.prop(scene, "thedas_io_filter_name", text="File name")
            self.layout.prop(scene, "thedas_io_filter_package", text="Package")
            self.layout.operator("thedas_io.refresh_assets", text="Search")
            self.layout.operator("thedas_io.scan_assets", text="Scan")
            if scene.thedas_io_scan_active:
                self.layout.prop(scene, "thedas_io_scan_progress", text="Scanning", slider=True)

            THEDAS_IO_UL_assets.draw_header(self.layout)
            self.layout.template_list(
                "THEDAS_IO_UL_assets",
                "",
                scene,
                "thedas_io_assets",
                scene,
                "thedas_io_index",
                rows=8,
            )

            self.layout.operator("thedas_io.import_asset", text="Import Selected")
        else:
            self.layout.prop(scene, "thedas_io_disk_file", text="File")
            self.layout.prop(scene, "thedas_io_disk_local", text="Resolve imports from local files")
            self.layout.operator("thedas_io.import_file", text="Import from file")


class THEDAS_IO_PT_export(bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "THEDAS_IO_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    def draw(self, context):
        scene = context.scene
        if self.layout is None:
            return
        self.layout.prop(scene, "thedas_io_export_dir", text="Export to")
        self.layout.prop(scene, "thedas_io_export_tint", text="Export tint")
        if scene.thedas_io_export_tint:
            self.layout.prop(scene, "thedas_io_tint_name", text="Tint resource")
        self.layout.prop(scene, "thedas_io_rename_export", text="Rename on export")

        THEDAS_IO_UL_export.draw_header(self.layout)
        self.layout.template_list(
            "THEDAS_IO_UL_export",
            "",
            scene,
            "thedas_io_export_items",
            scene,
            "thedas_io_export_index",
            rows=4,
        )

        if scene.thedas_io_rename_export:
            THEDAS_IO_UL_rename.draw_header(self.layout)
            self.layout.template_list(
                "THEDAS_IO_UL_rename",
                "rename",
                scene,
                "thedas_io_rename_items",
                scene,
                "thedas_io_rename_index",
                rows=4,
            )

        self.layout.operator("thedas_io.reload_textures", text="Reload textures from files")
        self.layout.operator("thedas_io.check_model", text="Check model")
        self.layout.operator("thedas_io.export_assets", text="Export")


class THEDAS_IO_PT_substance(bpy.types.Panel):
    bl_label = "Substance Painter"
    bl_idname = "THEDAS_IO_PT_substance"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    def draw(self, context):
        scene = context.scene
        if self.layout is None:
            return
        self.layout.prop(scene, "thedas_io_sp_dir", text="Folder")
        self.layout.prop(scene, "thedas_io_sp_split_packed", text="Split packed maps")
        self.layout.prop(scene, "thedas_io_sp_keep_resolution", text="Keep Substance resolution")

        self.layout.operator("thedas_io.export_substance", text="Export for Substance")
        self.layout.operator("thedas_io.import_substance", text="Import from Substance")


def _ext_filter(value) -> str | None:
    # none drops extension filter, find normalises case
    return None if not value or value == "NONE" else value


def _commit_edit_cages(context) -> str:
    # leave edit mode so cage edits reach the mesh datablock
    if not context.mode.startswith("EDIT") or context.view_layer.objects.active is None:
        return ""
    name = context.view_layer.objects.active.name
    bpy.ops.object.mode_set(mode="OBJECT")
    return name


class THEDAS_IO_OT_check_model(bpy.types.Operator):
    bl_idname = "thedas_io.check_model"
    bl_label = "Check DAO model"
    bl_description = (
        "Report what export would fix up silently or fail on: "
        "unweighted verts, missing materials and textures, "
        "degenerate faces, non-tri polygons, unapplied "
        "modifiers, shape keys and texture sizes."
    )

    def execute(self, context) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        try:
            rows = scene.thedas_io_export_items
        except AttributeError:
            rows = ()
        checked = {r.filename for r in rows if r.use} if rows else None
        if checked is not None and not checked:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}

        notes = check_model(connected(checked) if checked is not None else None)
        for note in notes:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"Checked model: {len(notes)} note(s)" if notes else "Model clean")
        return {"FINISHED"}


class THEDAS_IO_OT_export_assets(bpy.types.Operator):
    bl_idname = "thedas_io.export_assets"
    bl_label = "Export DAO assets"
    bl_description = (
        "Write every checked hierarchy as standalone files. "
        "Untouched resources come out byte-identical; edited "
        "meshes, materials and textures are re-encoded"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Export here instead of the Export to folder for this run",
    )

    def execute(self, context) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        try:
            rows = scene.thedas_io_export_items
        except AttributeError:
            rows = ()
        checked = {r.filename for r in rows if r.use} if rows else None
        if checked is not None and not checked:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}

        left = _commit_edit_cages(context)
        dest = bpy.path.abspath(self.directory or scene.thedas_io_export_dir)
        _push_renames(scene)
        written, errors = export_all(
            dest,
            scene.thedas_io_export_tint,
            scene.thedas_io_tint_name,
            connected(checked) if checked is not None else None,
        )

        scene.thedas_io_export_dir = dest
        for error in errors:
            self.report({"WARNING"}, error)
        if left:
            self.report({"INFO"}, f"Left Edit Mode on {left} so its edits export")
        self.report({"INFO"}, f"Exported {len(written)} files to {dest}")
        if errors:
            # partial override is broken, name missing files
            self.report({"ERROR"}, f"SKIPPED {len(errors)}: {'; '.join(errors)}")
        return {"FINISHED"}


def _checked_keys(scene) -> set | None:
    # checked roots, none means export all

    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return None
    checked = {r.filename for r in rows if r.use}
    if not checked:
        return None
    return connected(checked)


class THEDAS_IO_OT_export_substance(bpy.types.Operator):
    bl_idname = "thedas_io.export_substance"
    bl_label = "Export for Substance Painter"
    bl_description = (
        "Write model.obj + model.mtl plus unpacked PNG texture "
        "sets (DirectX normals, Specular-Glossiness layout) for "
        "painting in Substance Painter"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Export here instead of the Substance folder for this run",
    )

    def execute(self, context) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        dest = Path(bpy.path.abspath(self.directory or getattr(scene, "thedas_io_sp_dir", "")))
        if not str(dest):
            self.report({"WARNING"}, "Pick a Substance folder first")
            return {"CANCELLED"}

        scene.thedas_io_sp_dir = str(dest)
        keys = _checked_keys(scene)
        wanted = [k for k in ITEMS if keys is None or k in keys]
        split_packed = bool(getattr(scene, "thedas_io_sp_split_packed", True))

        # chunk to mao via live materials, material equals mao name
        chunks, maos = [], {}
        for key in wanted:
            item = ITEMS[key]
            if item.get("kind") != "msh":
                continue
            mesh = item.get("model")
            if mesh is None:
                continue
            for entry in item.get("objects") or []:
                obj, chunk = entry if isinstance(entry, (tuple, list)) else (entry, None)
                if chunk is None:
                    continue
                try:
                    decoded = decode_chunk(mesh, chunk)
                except (ValueError, ReferenceError):
                    continue
                mat_name = ""
                try:
                    mats = obj.data.materials
                    if mats:
                        mat_name = mats[0].name if mats[0] is not None else ""
                except (AttributeError, ReferenceError):
                    pass
                chunks.append((chunk.name or key, decoded, mat_name))
                if mat_name and mat_name not in maos:
                    hit = next(
                        (
                            ITEMS[k2]
                            for k2 in wanted
                            if ITEMS[k2].get("kind") == "mao"
                            and (ITEMS[k2].get("model") or {}).get("name") == mat_name
                        ),
                        None,
                    )
                    if hit is not None:
                        maos[mat_name] = hit

        if not chunks:
            self.report({"WARNING"}, "No meshes in the checked hierarchies")
            return {"CANCELLED"}

        dest.mkdir(parents=True, exist_ok=True)
        (dest / "model.obj").write_text(
            obj_text([(n, m, d) for n, d, m in chunks]), encoding="utf-8"
        )
        written, errors = ["model.obj"], []
        tex_dir = dest / "textures"
        mtl_maps: dict = {}
        for mat_name, item in maos.items():
            model = item.get("model")
            if not isinstance(model, dict):
                continue
            images = {}
            for slot, res in (model.get("textures") or {}).items():
                if not res:
                    continue
                src = next(
                    (
                        ITEMS[k2]
                        for k2 in wanted
                        if k2.lower() == str(res).lower() and ITEMS[k2].get("kind") == "texture"
                    ),
                    None,
                )
                if src is None:
                    errors.append(f"{mat_name}: {res} not imported; skipped")
                    continue
                try:
                    pil = PilImage.open(io.BytesIO(src["raw"]))
                    pil.load()
                    images[slot] = pil
                except (OSError, ValueError) as e:
                    errors.append(f"{mat_name}: {res}: {e}")
                    continue
            try:
                maps = unpack_images(model, images, split_packed)
            except (OSError, ValueError, KeyError) as e:
                errors.append(f"{mat_name}: unpack failed: {e}")
                continue

            out_dir = tex_dir / mat_name
            out_dir.mkdir(parents=True, exist_ok=True)
            plan = unpack_plan(model, split_packed)
            for slot in model.get("textures") or {}:
                stem, note = plan.get(slot, (None, None))
                if stem is None and note:
                    errors.append(f"{mat_name}: {slot}: {note}")

            for stem, pil in maps.items():
                path = out_dir / f"{stem}.png"
                pil.save(path, "PNG")
                written.append(str(path.relative_to(dest)))
            mtl_maps[mat_name] = set(maps)

            # meshmap copy for substance auto-assign, do not rebake in substance
            if MAP_NORMAL in maps:
                meshmap_dir = dest / "meshmaps"
                meshmap_dir.mkdir(parents=True, exist_ok=True)
                meshmap = meshmap_dir / f"{mat_name}_normal_base.png"
                maps[MAP_NORMAL].save(meshmap, "PNG")
                written.append(str(meshmap.relative_to(dest)))

        (dest / "model.mtl").write_text(
            mtl_text([(m, mtl_maps.get(m, ())) for m in maos]), encoding="utf-8"
        )
        written.append("model.mtl")

        for error in errors:
            self.report({"WARNING"}, error)
        self.report(
            {"INFO"},
            f"Substance set: {len(written)} files in {dest} "
            f"(DirectX normals, Specular-Glossiness template)",
        )
        return {"FINISHED"}


class THEDAS_IO_OT_import_substance(bpy.types.Operator):
    bl_idname = "thedas_io.import_substance"
    bl_label = "Import from Substance Painter"
    bl_description = (
        "Read the $textureSet_$map PNGs Substance exported back "
        "into DAO layout at each slot's source resolution (or "
        "the painted size with Keep Substance resolution), then "
        "reload them into the live images"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Read Substance output here instead of the Substance folder for this run",
    )

    def execute(self, context) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        base = Path(bpy.path.abspath(self.directory or getattr(scene, "thedas_io_sp_dir", "")))
        src = base / "textures"
        if not src.is_dir():
            self.report({"WARNING"}, f"No textures/ folder in {base} (export for Substance first)")
            return {"CANCELLED"}

        split_packed = bool(getattr(scene, "thedas_io_sp_split_packed", True))
        keep_size = bool(getattr(scene, "thedas_io_sp_keep_resolution", False))
        folder = _cache_dir()

        def read_png(png):
            with PilImage.open(png) as im:
                im.load()
                return im.copy()

        have = collect_sp_maps(src)
        mao_names = [
            ((item.get("model") or {}).get("name") or "")
            for item in ITEMS.values()
            if item.get("kind") == "mao" and isinstance(item.get("model"), dict)
        ]
        have, bare_warnings = attribute_bare(have, mao_names)
        done, failed = list(bare_warnings), []

        for item in ITEMS.values():
            if item.get("kind") != "mao":
                continue
            model = item.get("model")
            if not isinstance(model, dict):
                continue
            mat_name = model.get("name") or ""
            paths = have.get(mat_name)
            if not paths:
                continue  # material sp never saw, source bytes stand

            sp = {}
            for stem, png in paths.items():
                try:
                    sp[stem] = read_png(png)
                except OSError as e:
                    failed.append(f"{mat_name}/{png.name}: {e}")

            # dao maps differ per map, sp exports uniform, keep source alpha

            sizes, sources = {}, {}
            for slot, res in (model.get("textures") or {}).items():
                if not res:
                    continue
                src_item = next(
                    (
                        ITEMS[k2]
                        for k2 in ITEMS
                        if k2.lower() == str(res).lower() and ITEMS[k2].get("kind") == "texture"
                    ),
                    None,
                )
                if src_item is None:
                    continue
                try:
                    with PilImage.open(io.BytesIO(src_item["raw"])) as im:
                        im.load()
                        sizes[slot] = tuple(im.size)
                        sources[slot] = im.copy()
                except OSError:
                    continue

            try:
                repacked = repack_images(
                    model, sp, split_packed, None if keep_size else sizes or None, sources or None
                )
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                failed.append(f"{mat_name}: repack failed: {e}")
                continue

            if keep_size:
                skipped = _scale_live_images(model, repacked, failed)
            else:
                skipped = set()

            for slot, pil in repacked.items():
                if slot in skipped:
                    continue
                res = (model.get("textures") or {}).get(slot)
                if not res:
                    continue
                # dao-layout pixels into cache, reload path re-packs into blend
                try:
                    (folder / Path(res).name).parent.mkdir(parents=True, exist_ok=True)
                    pil.save(folder / Path(res).name, "PNG")
                except OSError as e:
                    failed.append(f"{mat_name}: {res}: {e}")
                    continue
                done.append(f"{mat_name}/{res}")

        reloaded, _, reload_failed = reload_textures()
        failed.extend(reload_failed)
        for note in failed:
            self.report({"WARNING"}, note)

        if not done and not reloaded:
            self.report({"WARNING"}, "Nothing to import: no <material>/*.png found")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {len(done)} map(s) from {src}")
        return {"FINISHED"}


def _root_alive(item: dict) -> bool:
    # true while file objects still exist in blend
    arm = item.get("armature")
    if item.get("kind") == "mmh" and arm is not None:
        try:
            _ = arm.name
        except ReferenceError:
            return False

    for obj in _viewport_objects(item):
        try:
            _ = obj.name
        except ReferenceError:
            continue
        return True
    return False


def _prune_export_items(scene) -> bool:
    # drop rows with deleted objects, never call from draw

    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return False

    stale = [
        i
        for i, r in enumerate(rows)
        if r.filename not in ITEMS or not _root_alive(ITEMS[r.filename])
    ]
    for i in reversed(stale):
        rows.remove(i)

    if stale:
        _sync_rename_items(scene)  # their files leave the export too
    return bool(stale)


def _show_materials() -> None:
    # put viewports in material preview, only if still on solid

    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.shading.type == "SOLID":  # type: ignore
                    space.shading.type = "MATERIAL"  # type: ignore


def _sync_export_items(scene) -> None:
    # object list mirrors hierarchy roots, never call from draw

    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return

    have = {r.filename: r for r in rows}
    alive = set()
    for key in roots():
        item = ITEMS[key]
        if not _root_alive(item):
            continue
        alive.add(key)
        if key not in have:
            rows.add().filename = key

    for i in reversed([i for i, r in enumerate(rows) if r.filename not in alive]):
        rows.remove(i)
    _sync_rename_items(scene)


def _sync_rename_items(scene) -> None:
    # rename table lists exported files of checked roots

    try:
        rows = scene.thedas_io_export_items
        renames = scene.thedas_io_rename_items
    except AttributeError:
        return

    checked = {r.filename for r in rows if getattr(r, "use", True)}
    wanted: list = []
    for key in connected(checked):
        if key not in wanted:
            wanted.append(key)

    if getattr(scene, "thedas_io_export_tint", False):
        tint_name = getattr(scene, "thedas_io_tint_name", "") or ""
        for key in checked:
            item = ITEMS.get(key)
            if item is not None and item.get("kind") == "mmh":
                tnt = _tnt_name(item, tint_name)
                if tnt not in wanted:
                    wanted.append(tnt)

    keep = {r.filename: getattr(r, "export_name", "") for r in renames}
    for i in reversed(range(len(renames))):
        renames.remove(i)
    for key in wanted:
        row = renames.add()
        row.filename = key
        if keep.get(key):
            row.export_name = keep[key]


def _push_renames(scene) -> None:
    # export-name edits into items before export

    if not getattr(scene, "thedas_io_rename_export", False):
        for item in ITEMS.values():
            item.pop("as", None)
            item.pop("as_tnt", None)
        return

    _sync_rename_items(scene)  # fresh rows (tint name edits included), still pre-export
    try:
        rows = scene.thedas_io_rename_items
    except AttributeError:
        return

    tinting = getattr(scene, "thedas_io_export_tint", False)
    tint_name = getattr(scene, "thedas_io_tint_name", "") or ""
    tnt_of: dict = {}
    if tinting:
        for key, item in ITEMS.items():
            if item.get("kind") == "mmh":
                tnt_of[_tnt_name(item, tint_name).lower()] = key

    for r in rows:
        name = getattr(r, "filename", "")
        new = (getattr(r, "export_name", "") or "").strip()
        hit = ITEMS.get(name)
        if hit is not None:
            hit["as"] = new
        elif name.lower() in tnt_of:
            # renamed tint belongs to its mmh, row wins over tint_name
            mmh = ITEMS[tnt_of[name.lower()]]
            mmh["as_tnt"] = new if not new or new.lower().endswith(".tnt") else f"{new}.tnt"


_PRUNING = False


def _on_depsgraph_update(scene, depsgraph=None) -> None:
    # keep object list honest on viewport deletes, guard stops retrigger
    global _PRUNING
    if _PRUNING:
        return

    if not ITEMS:
        return

    _PRUNING = True
    try:
        for one in bpy.data.scenes:
            if _prune_export_items(one):
                _sync_rename_items(one)
    except (AttributeError, ReferenceError, RuntimeError):
        pass  # mid-teardown scene data; the next update retries
    finally:
        _PRUNING = False


class THEDAS_IO_AddonPreferences(bpy.types.AddonPreferences):
    # must be runtime module name, plain package in tests but blender
    bl_idname = __package__ or "thedas_io"
    bl_label = "ThedasIO"
    thedas_io_game_dir: bpy.props.StringProperty(  # type: ignore
        name="Game folder",
        subtype="DIR_PATH",
        default="",
        description="Dragon Age: Origins install folder; Scan indexes it when no folder is given",
    )

    def draw(self, context):
        if self.layout is None:
            return
        self.layout.prop(self, "thedas_io_game_dir")


def _prefs_game_dir(context) -> str:
    try:
        addons = context.preferences.addons
    except AttributeError:
        return ""
    candidates = []
    for key in (__package__ or "", "thedas_io"):
        try:
            candidates.append(addons[key])
        except (KeyError, TypeError):
            continue
    try:
        candidates.extend(a for a in addons if all(a is not c for c in candidates))
    except TypeError:
        pass
    for addon in candidates:
        game_dir = getattr(getattr(addon, "preferences", None), "thedas_io_game_dir", "") or ""
        if game_dir:
            return str(game_dir)
    return ""


def _fill_assets(scene, db_path=None, ext=None, name=None, package=None) -> list:
    if isinstance(ext, str):
        ext = ext.strip() or None

    rows = find(ext, name, package, db_path)
    scene.thedas_io_assets.clear()

    for r in rows:
        item = scene.thedas_io_assets.add()
        item.package = r["package"] or ""
        item.filename = r["filename"]
        item.ext = r["ext"]
        item.path = r["path"]
    return rows


def _scan_async(root, db_path) -> dict:
    # scanner scan in worker thread, poll current/total/done

    state: dict = {
        "done": False,
        "current": 0,
        "total": 0,
        "stats": None,
        "error": None,
        "thread": None,
    }

    def work():
        try:

            def progress(done, total):
                state["current"] = done
                state["total"] = total

            state["stats"] = scan(root, db_path, progress=progress)
        except (OSError, ValueError, struct.error, sqlite3.Error) as exc:
            state["error"] = exc
        finally:
            state["done"] = True

    thread = threading.Thread(target=work, daemon=True)
    state["thread"] = thread
    thread.start()
    return state


class THEDAS_IO_OT_scan_assets(bpy.types.Operator):
    bl_idname = "thedas_io.scan_assets"
    bl_label = "Scan DAO packages"
    bl_description = (
        "Index every .rim/.erf archive plus loose models and "
        "textures under the scan folder into the asset database"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Scan here instead of the game folder for this run",
    )
    _timer = None
    _state = None
    _db_path = ""
    _scene = None

    def _resolve(self, context):
        root = bpy.path.abspath(getattr(self, "directory", "") or _prefs_game_dir(context))
        if not root:
            return None, "Set a scan folder or the game folder in Preferences"
        if not Path(str(root)).exists():
            return None, f"Scan folder not found: {root}"
        return str(root), ""

    def invoke(self, context, event) -> set:
        # scan in worker thread, progress via modal timer
        if getattr(context.scene, "thedas_io_scan_active", False):
            self.report({"WARNING"}, "A scan is already running")
            return {"CANCELLED"}

        root, err = self._resolve(context)
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}

        scene = context.scene
        self._db_path = str(scene.thedas_io_db_path or "")
        self._scene = scene
        self._state = _scan_async(root, self._db_path or None)
        scene.thedas_io_scan_active = True
        scene.thedas_io_scan_progress = 0

        try:
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.1, window=context.window)
        except (AttributeError, RuntimeError, TypeError):
            self._state["thread"].join()  # no event loop: finish synchronously
            return self._finish(context)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context) -> set:
        # synchronous path for scripts, background mode, tests
        if getattr(context.scene, "thedas_io_scan_active", False):
            self.report({"WARNING"}, "A scan is already running")
            return {"CANCELLED"}

        root, err = self._resolve(context)
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}

        self._db_path = str(context.scene.thedas_io_db_path or "")
        self._scene = context.scene
        self._state = _scan_async(root, self._db_path or None)
        self._state["thread"].join()
        return self._finish(context)

    def modal(self, context, event) -> set:
        if event.type == "ESC":
            self._cancel_timer(context)
            if self._scene is not None:
                self._scene.thedas_io_scan_active = False
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        return self._tick(context)

    def _tick(self, context) -> set:
        # one progress tick, callable without event pump
        state = self._state or {}
        scene = self._scene or context.scene
        total = state.get("total") or 0
        scene.thedas_io_scan_progress = int(100 * state.get("current", 0) / total) if total else 0

        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()

        if not state.get("done"):
            return {"RUNNING_MODAL"}
        self._cancel_timer(context)
        return self._finish(context)

    def _finish(self, context) -> set:
        scene = self._scene or context.scene
        scene.thedas_io_scan_active = False
        state = self._state or {}
        if state.get("error") is not None:
            self.report({"WARNING"}, f"Scan failed: {state['error']}")
            return {"CANCELLED"}

        rows = _fill_assets(
            scene,
            self._db_path or None,
            _ext_filter(scene.thedas_io_filter_ext),
            scene.thedas_io_filter_name or None,
            scene.thedas_io_filter_package or None,
        )
        stats = state.get("stats") or {}
        self.report(
            {"INFO"},
            f"Scanned {stats.get('files', 0)} archive files, "
            f"{stats.get('entries', 0)} entries, {len(rows)} assets",
        )
        return {"FINISHED"}

    def _cancel_timer(self, context):
        wm = getattr(context, "window_manager", None)
        if wm is not None and self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except (AttributeError, RuntimeError):
                pass
            self._timer = None

    def cancel(self, context):
        self._cancel_timer(context)
        if self._scene is not None:
            self._scene.thedas_io_scan_active = False


class THEDAS_IO_OT_refresh_assets(bpy.types.Operator):
    bl_idname = "thedas_io.refresh_assets"
    bl_label = "Refresh DAO assets"
    bl_description = (
        "Re-run the extension/name/package filters against the "
        "asset database without rescanning the archives"
    )

    def execute(self, context) -> set:
        scene = context.scene
        rows = _fill_assets(
            scene,
            scene.thedas_io_db_path or None,
            _ext_filter(scene.thedas_io_filter_ext),
            scene.thedas_io_filter_name or None,
            scene.thedas_io_filter_package or None,
        )

        self.report({"INFO"}, f"{len(rows)} assets")
        return {"FINISHED"}


class THEDAS_IO_OT_import_asset(bpy.types.Operator):
    bl_idname = "thedas_io.import_asset"
    bl_label = "Import DAO asset"
    bl_description = (
        "Import the selected asset with its sibling mesh, "
        "material and textures resolved from the database"
    )

    def execute(self, context) -> set:
        scene = context.scene
        try:
            item = scene.thedas_io_assets[scene.thedas_io_index]
        except IndexError:
            self.report({"WARNING"}, "No asset selected")
            return {"CANCELLED"}
        row = {
            "package": item.package or None,
            "filename": item.filename,
            "ext": item.ext,
            "path": item.path,
        }
        result = _import_row(context, row, scene.thedas_io_db_path or None, False, self.report)

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result


class THEDAS_IO_OT_import_file(bpy.types.Operator):
    bl_idname = "thedas_io.import_file"
    bl_label = "Import DAO file"
    bl_description = (
        "Import one .msh/.mmh file from disk, resolving its "
        "siblings next to the file instead of from the game"
    )

    def execute(self, context) -> set:
        scene = context.scene
        path = bpy.path.abspath(scene.thedas_io_disk_file or "")
        if not path or not Path(path).is_file():
            self.report({"WARNING"}, "Pick a .msh or .mmh file first")
            return {"CANCELLED"}
        p = Path(path)
        row = {"package": None, "filename": p.name, "ext": p.suffix, "path": str(p)}
        result = _import_row(context, row, None, scene.thedas_io_disk_local, self.report)

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result


def _import_row(context, row: dict, db, local_only: bool, emit) -> set:
    # shared msh/mmh import for one row, emit reports to user

    local = extract(row)
    kind = importer_for(row["ext"])
    if kind == "msh":
        raw = local.read_bytes()
        mesh = MSHReader(raw).mesh
        objs = [
            build_chunk_object(context, mesh, chunk, raw, row["filename"])
            for chunk in mesh.chunks  # type: ignore
        ]
        notes: list = []
        stem = Path(row["filename"]).stem
        mao = resolve(f"{stem}.mao", row, db, local_only)
        if mao is None:
            notes.append(
                f"No MAO found for {stem}.mao (an .msh names no material; "
                "import its .mmh for textures)"
            )
        else:
            apply_materials(mao, objs, row=row, db_path=db, notes=notes, local_only=local_only)

        for note in dict.fromkeys(notes):
            emit({"WARNING"}, note)
        emit({"INFO"}, f"Imported {len(mesh.chunks)} chunks from {row['filename']}")  # type: ignore
        return {"FINISHED"}
    if kind == "mmh":
        raw = local.read_bytes()
        tree = MMHReader(raw).tree

        chunks = {}
        msh_name = str(tree.values.get(6005) or "")  # type: ignore
        sibling = resolve(msh_name, row, db, local_only)
        if sibling is not None and sibling.is_file():
            sraw = sibling.read_bytes()
            smesh = MSHReader(sraw).mesh
            for chunk in smesh.chunks:  # type: ignore
                chunks[chunk.name] = build_chunk_object(context, smesh, chunk, sraw, msh_name)

        reg = remember(row["filename"], "mmh", tree, raw)
        stats = build_hierarchy(context, tree, chunks)
        reg["hosts"] = stats["hosts"]
        reg["armature"] = stats["armature"]
        reg["meshes"] = list(chunks.values())  # tint export reads their materials
        notes = []
        stem = Path(row["filename"]).stem
        for name in stats["linked"]:
            node = tree.find(name)  # type: ignore
            linked = chunks.get(node.values.get(6006))
            if linked is not None:
                mesh_mao, tried = _mao_for_node(node, stem, row, db, local_only)
                if mesh_mao is None:
                    notes.append(f"No MAO found for {', '.join(tried)} (scan its package first)")
                else:
                    apply_materials(
                        mesh_mao,
                        [linked],
                        node.values.get(6335),
                        row=row,
                        db_path=db,
                        notes=notes,
                        local_only=local_only,
                    )
        note = f", missing chunks: {stats['missing']}" if stats["missing"] else ""
        for warning in dict.fromkeys(notes):
            emit({"WARNING"}, warning)
        emit(
            {"INFO"},
            f"Imported {tree.name}: "  # type: ignore
            f"{stats['bones']} bones, {stats['empties']} empties, "
            f"{len(stats['linked'])} meshes{note}",
        )
        return {"FINISHED"}
    emit({"WARNING"}, f"No importer for {row['filename']}")
    return {"CANCELLED"}


def _mao_for_node(node, stem: str, row: dict, db_path=None, local_only: bool = False):
    # mao file for one mshh node, tries verbatim then lowered

    tried = []
    matobj = node.values.get(6001)
    if matobj:
        tried.append(f"{matobj}.mao")
    tried.append(f"{stem}.mao")

    for name in dict.fromkeys(tried):
        hit = resolve(name, row, db_path, local_only)
        if hit is not None:
            return hit, tried
    return None, tried


def apply_materials(
    mao_path, objs, color=None, row=None, db_path=None, notes=None, local_only: bool = False
):
    # parsed mao onto chunk objects, silent when absent

    if mao_path is None:
        return None

    mao_path = Path(mao_path)
    if not mao_path.is_file():
        # mao in archive, pull raw bytes from loaded db items

        mao_bytes = None
        want = mao_path.name.lower()
        for item in _LOADED_ITEMS:
            if item["file"].lower() == want:
                mao_bytes = item["raw"]
                break
        if mao_bytes is None:
            if notes is not None:
                notes.append(f"MAO not found: {mao_path.name} (scan its package first)")
            return None
        parsed = parse_mao(mao_bytes.decode("utf-8"))

        item = remember(mao_path.name, "mao", parsed, mao_bytes)
    else:
        text = mao_path.read_text(encoding="utf-8")
        parsed = parse_mao(text)
        item = remember(mao_path.name, "mao", parsed, text.encode("utf-8"))

    images = {}
    for semantic, res in (parsed.get("textures") or {}).items():
        if not res:
            continue
        image = _resolve_texture(res, mao_path, row, db_path, local_only)
        if image is not None:
            images[semantic] = image
    missing = [
        res
        for semantic, res in (parsed.get("textures") or {}).items()
        if res and semantic not in images
    ]
    if missing and notes is not None:
        notes.append(f"{mao_path.name}: textures not found: {', '.join(missing)}")

    for semantic, res in (parsed.get("textures") or {}).items():
        if res and semantic not in images:
            images[semantic] = _missing_image(res, images)

    tint = _existing_tint(mao_path.stem, row, db_path, local_only)
    if (
        tint is None
        and notes is not None
        and any(role == "tint" for role in slot_roles(parsed.get("textures")).values())
    ):
        notes.append(
            f"{mao_path.name}: tint mask present but no {mao_path.stem}.tnt "
            f"found; preview is untinted while the game tints via the item variation"
        )

    mat = build_material(parsed.get("name") or mao_path.stem, parsed, images, color, tint)
    item["objects"].append(mat)  # live material for mao edit-back
    for obj in objs:
        obj.data.materials.append(mat)
    return mat


def _seed_tint(group, name: str, colour) -> None:
    # push one tnt value into group, colour plus alpha float

    if name not in group.inputs:
        return
    alpha = group.inputs.get(alpha_input(name) or "")
    if alpha is None:
        group.inputs[name].default_value = tuple(colour)
        return
    group.inputs[name].default_value = tuple(colour)[:3] + (1.0,)  # mewo im a cat :33
    alpha.default_value = float(colour[3])


def _existing_tint(
    stem: str, row: dict | None, db_path=None, local_only: bool = False
) -> dict | None:
    # tint values from stem.tnt next to mao or in db

    if not stem or row is None:
        return None

    try:
        path = resolve(f"{stem}.tnt", row, db_path, local_only)
        return parse(path.read_bytes()) if path is not None and path.is_file() else None
    except (OSError, ValueError):
        return None


def _resolve_texture(res, mao_path, row=None, db_path=None, local_only: bool = False):
    # one resname to blender image via sibling, db or loaded bytes

    source = mao_path.parent / res
    if source.is_file():
        return load_image(source)
    if row is not None and not row.get("package"):
        sibling = Path(row["path"]).parent / res
        if sibling.is_file():
            return load_image(sibling)

    anchor = (
        dict(row)
        if row
        else {
            "package": None,
            "filename": mao_path.name,
            "ext": mao_path.suffix,
            "path": str(mao_path),
        }
    )
    try:
        hit = resolve(res, anchor, db_path, local_only)
    except (OSError, ValueError, KeyError, sqlite3.Error):
        hit = None
    if hit is not None and Path(hit).is_file():
        return load_image(hit)

    # texture in archive, pull from loaded db items

    tex_bytes = None
    want = Path(res).name.lower()
    for item in _LOADED_ITEMS:
        if item["file"].lower() == want:
            tex_bytes = item["raw"]
            break
    if tex_bytes is None:
        return None
    pil = PilImage.open(io.BytesIO(tex_bytes))
    pil.load()
    return _new_image(Path(res).name, pil, tex_bytes)


_CACHE_DIR_NAME = "thedas_io_textures"
_CACHE_NOTED = False


def _cache_dir() -> Path:
    # texture copies beside blend, else blender temp dir
    global _CACHE_NOTED

    base = Path(bpy.data.filepath).parent if bpy.data.filepath else Path(bpy.app.tempdir)
    out = base / _CACHE_DIR_NAME

    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"DAO: no texture cache at {out}: {e}")

    if not _CACHE_NOTED and out.is_dir():
        _CACHE_NOTED = True
        print(f"DAO: texture cache at {out} (edit these to change a texture)")
    return out


def _repack_textures(scene=None, depsgraph=None) -> None:
    # keep packed pixels equal to screen before save
    for image, _ in _texture_images():
        try:
            if image.packed_file is not None:
                image.pack()
        except (AttributeError, ReferenceError, RuntimeError):
            continue

    for one in bpy.data.scenes:
        _store_registry(one)


def _texture_images() -> list:
    # images with editable copy in cache, found via bpy data

    folder = _cache_dir()
    out = []

    for image in bpy.data.images:
        try:
            name = image.name
        except ReferenceError:
            continue
        if name and (folder / name).is_file():
            out.append((image, name))
    return out


def reload_textures() -> tuple[list, list, list]:
    # re-read cached textures from disk and re-pack

    folder = _cache_dir()
    done, missing, failed = [], [], []

    for image, name in _texture_images():
        path = folder / name
        if not path.is_file():
            missing.append(name)
            continue

        try:
            with PilImage.open(path) as im:
                im.load()
                pil = im.copy()
        except OSError as e:
            failed.append(f"{name}: {e}")
            continue

        if tuple(pil.size) != tuple(image.size):
            try:
                image.scale(*pil.size)
            except (AttributeError, ReferenceError, RuntimeError) as e:
                failed.append(f"{name}: cannot rescale live image: {e}")
                continue

        try:
            _blit(image, pil, role_of(name))
            image.pack()
            done.append(name)
        except (AttributeError, ReferenceError, RuntimeError) as e:
            failed.append(f"{name}: {e}")
    return done, missing, failed


class THEDAS_IO_OT_reload_textures(bpy.types.Operator):
    bl_idname = "thedas_io.reload_textures"
    bl_label = "Reload textures from files"
    bl_description = (
        "Re-read the editable copies in thedas_io_textures/ (painted "
        "externally, e.g. in GIMP) back into the live images"
    )

    def execute(self, context) -> set:
        done, missing, failed = reload_textures()
        for note in failed:
            self.report({"WARNING"}, note)

        if not done and missing:
            self.report({"WARNING"}, f"No cached files in {_cache_dir()} - import the asset again")

        self.report(
            {"INFO"},
            f"Reloaded {len(done)} texture(s) from {_cache_dir()}"
            + (f"; {len(missing)} had no file" if missing else ""),
        )
        return {"FINISHED"} if done else {"CANCELLED"}


def _move_file(src: Path, dest: Path) -> None:
    # move src onto dest, copy fallback across drives
    try:
        src.replace(dest)
    except OSError:
        shutil.copy2(src, dest)
        src.unlink()


def _relocate_cache(scene=None, depsgraph=None) -> None:
    # move temp cache beside blend once saved

    if not bpy.data.filepath:
        return
    src = Path(bpy.app.tempdir) / _CACHE_DIR_NAME
    if not src.is_dir():
        return
    dest = Path(bpy.data.filepath).parent / _CACHE_DIR_NAME
    if dest == src:
        return

    try:
        dest.mkdir(parents=True, exist_ok=True)
        for f in list(src.iterdir()):
            target = dest / f.name
            if not target.exists():
                _move_file(f, target)  # never clobber edit already there
    except OSError as e:
        print(f"DAO: could not move the texture cache: {e}")
        return
    try:
        src.rmdir()
    except OSError:
        pass
    print(f"DAO: texture cache moved to {dest}")


def _scale_live_images(model: dict, repacked: dict, failed: list) -> set:
    # resize live images to repacked sizes, keep-resolution only

    try:
        images = list(bpy.data.images)
    except (AttributeError, ReferenceError):
        return set()

    by_name: dict = {}
    for image in images:
        try:
            by_name[image.name] = image
        except ReferenceError:
            continue

    textures = model.get("textures") or {}
    skipped: set = set()

    for slot, pil in repacked.items():
        res = textures.get(slot)
        if not res:
            continue
        image = by_name.get(Path(res).name)
        if image is None:
            continue
        try:
            size = tuple(image.size)
        except (AttributeError, ReferenceError):
            continue
        if size != tuple(pil.size):
            try:
                image.scale(*pil.size)
            except (AttributeError, ReferenceError, RuntimeError) as e:
                failed.append(f"{res}: cannot rescale live image: {e}")
                skipped.add(slot)
    return skipped


def _blit(image, pil, role: str | None) -> None:
    # push decoded dao pixels into image, normals unswizzled

    shown = thedas_io_normals_to_rgba(pil) if role == "normal" else pil.convert("RGBA")
    flat = np.asarray(shown).astype(np.float32).reshape(-1) / 255.0
    image.pixels.foreach_set(flat)
    image.update()


def _new_image(name, pil, raw):
    # blender image from pillow pixels, stashes source bytes

    cached = _cache_dir() / Path(name).name
    if cached.is_file():
        try:  # edited copy wins
            with PilImage.open(cached) as im:
                im.load()
                pil = im
        except OSError:
            pass
    else:
        try:
            cached.write_bytes(raw)
        except OSError:
            pass

    role = role_of(name)
    image: Any = bpy.data.images.new(name, pil.width, pil.height, alpha=True)
    image.alpha_mode = "CHANNEL_PACKED"
    colorspace: Any = image.colorspace_settings
    colorspace.name = "sRGB" if role == "diffuse" else "Non-Color"

    _blit(image, pil, role)
    try:
        image.pack()  # pixels ride along in blend
    except RuntimeError:
        pass
    remember(name, "texture", None, raw)["objects"].append(image)
    return image


def load_image(path):
    # dds/tga file to blender image, stashes source bytes

    raw = Path(path).read_bytes()
    pil = PilImage.open(io.BytesIO(raw))
    pil.load()
    return _new_image(Path(path).name, pil, raw)


def _missing_image(res, images):
    # magenta stand-in for unresolvable resname, tagged to skip export

    sizes = []
    for im in images.values():
        try:
            sizes.append(tuple(im.size))
        except (AttributeError, ReferenceError, TypeError):
            continue

    w, h = max(sizes, key=lambda s: s[0] * s[1]) if sizes else (64, 64)
    name = Path(res).name
    image: Any = bpy.data.images.new(name, w, h, alpha=True)
    image.alpha_mode = "CHANNEL_PACKED"
    colorspace: Any = image.colorspace_settings
    colorspace.name = "sRGB" if role_of(name) == "diffuse" else "Non-Color"

    flat = np.zeros(w * h * 4, dtype=np.float32)
    flat[0::4] = 1.0
    flat[2::4] = 1.0
    flat[3::4] = 1.0
    image.pixels.foreach_set(flat)
    image.update()

    try:
        image.pack()
    except RuntimeError:
        pass

    try:
        image["thedas_io_missing"] = res
    except (AttributeError, TypeError):
        pass
    return image


def build_material(name, mao, images, color=None, tint=None):
    # specular-bsdf wiring from dao channel packing
    mat: Any = bpy.data.materials.new(name)
    mat.use_nodes = True
    _wire_material(mat, mao, images, color=color, tint=tint)

    for key, values in (mao.get("vectors") or {}).items():
        mat[key] = list(values)
    return mat


# fixed slots keep built materials arranged the same way
_POS_UV = (-220.6, 72.9)
_POS_MAPPING = (-17.4, 77.2)
_POS_OUT = (1301.0, 46.2)
_POS_BSDF = (1111.0, 102.7)
_POS_TEX = {
    "diffuse": (352.6, 319.2),
    "normal": (352.6, -202.8),
    "specular": (352.6, 58.2),
    "tint": (352.6, 580.2),
    "emission": (352.6, -460.0),
    "packed": (352.6, 440.0),
}
_POS_PACKED_SEP = (180.0, 440.0)
_POS_NMAP = (788.1, -158.8)
_POS_GLOSS_INV = (796.4, -10.2)
_POS_SPEC_GAMMA = (950.0, 250.0)
_POS_PRINCIPLED = (1111.0, -620.0)
_POS_OUT_CYCLES = (1301.0, -620.0)
_POS_TRANS_INV = (796.4, -300.0)
_POS_GROUP = {"Diffuse": (769.6, 333.7), "Specular": (769.6, 733.7)}
_POS_MIX6335 = (975.0, -160.0)


def _wire_material(mat, mao, images, color=None, tint=None):
    by_role: dict = {}
    packed: dict = {}
    roles = slot_roles(mao.get("textures"))
    for semantic, image in images.items():
        role = roles.get(semantic)
        if role == "diffuse" and "pack" in str(semantic).lower():
            packed[semantic] = image
        else:
            by_role.setdefault(role, image)

    nodes = mat.node_tree.nodes
    if "Principled BSDF" in nodes:
        nodes.remove(nodes["Principled BSDF"])
    out = nodes.get("Material Output", None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
    out.location = _POS_OUT
    out.target = "EEVEE"
    bsdf = nodes.new("ShaderNodeEeveeSpecular")
    bsdf.location = _POS_BSDF
    links = mat.node_tree.links
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    # viewport flip here so export stays byte-identical
    uvmap = nodes.new("ShaderNodeTexCoord")
    uvmap.location = _POS_UV
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = _POS_MAPPING
    mapping.inputs["Scale"].default_value = (1.0, -1.0, 1.0)
    links.new(uvmap.outputs["UV"], mapping.inputs["Vector"])
    vector = mapping.outputs["Vector"]
    base_feed = None
    normal_feed = None
    rough_feed = None
    trans_feed = None
    emission_feed = None
    diffuse = by_role.get("diffuse")
    spec = by_role.get("specular")
    if diffuse is not None:
        tex_diffuse = _image_node(mat, diffuse, vector, _POS_TEX["diffuse"])
        base_feed = tex_diffuse.outputs["Color"]
        if _has_alpha(diffuse):
            if _transparent_semantic(mao):
                trans_feed = _to_transparency(
                    mat, bsdf, tex_diffuse.outputs["Alpha"], cutout=_cutout_semantic(mao)
                )
            elif spec is None:
                # face-style map, alpha is the specular map
                links.new(tex_diffuse.outputs["Alpha"], bsdf.inputs["Specular"])
            # opaque with spec map, alpha is not opacity
    for image in packed.values():
        tex = _image_node(mat, image, vector, _POS_TEX["packed"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = _POS_PACKED_SEP
        links.new(tex.outputs["Color"], sep.inputs["Color"])
        base_feed = tex.outputs["Alpha"]
        if spec is None:
            links.new(sep.outputs["Green"], bsdf.inputs["Specular"])
        if _channel_varies(image, 2):
            trans_feed = _to_transparency(mat, bsdf, sep.outputs["Blue"])

    tint_image = by_role.get("tint")
    tint_groups = {}
    if tint_image is not None and base_feed is not None:
        tex = _image_node(mat, tint_image, vector, _POS_TEX["tint"])
        for side in ("Diffuse", "Specular"):
            group = nodes.new("ShaderNodeGroup")
            group.node_tree = _tint_group(side)
            group.location = _POS_GROUP[side]
            links.new(tex.outputs["Color"], group.inputs["Tint mask (RGB)"])
            links.new(tex.outputs["Alpha"], group.inputs["Tint mask (A)"])
            for key, value in (tint or {}).items():
                _seed_tint(group, key, value)
            tint_groups[side] = group
        links.new(base_feed, tint_groups["Diffuse"].inputs["Diffuse"])
        base_feed = tint_groups["Diffuse"].outputs["Result"]

    if base_feed is not None:
        if color is not None and all(math.isfinite(c) for c in color):
            mix = nodes.new("ShaderNodeMix")
            mix.location = _POS_MIX6335
            mix.blend_type = "MULTIPLY"
            mix.data_type = "RGBA"
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs["B"].default_value = (*color[:3], 1.0)
            links.new(base_feed, mix.inputs["A"])
            base_feed = mix.outputs["Result"]
        links.new(base_feed, bsdf.inputs["Base Color"])

    normal = by_role.get("normal")
    if normal is not None:
        tex = _image_node(mat, normal, vector, _POS_TEX["normal"])
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = _POS_NMAP
        nmap.width = 160.0
        links.new(tex.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        normal_feed = nmap.outputs["Normal"]

    if spec is not None:
        tex = _image_node(mat, spec, vector, _POS_TEX["specular"])
        tint_specular = tint_groups.get("Specular")
        if tint_specular is not None:
            # tint the highlight per mask channel, same as the base colour
            links.new(tex.outputs["Color"], tint_specular.inputs["Specular"])
            spec_feed = tint_specular.outputs["Result"]
        else:
            spec_feed = tex.outputs["Color"]
        # preview-only calibration, export reads image names never links
        gamma = nodes.new("ShaderNodeGamma")
        gamma.location = _POS_SPEC_GAMMA
        gamma.inputs["Gamma"].default_value = 2.5
        links.new(spec_feed, gamma.inputs["Color"])
        links.new(gamma.outputs["Color"], bsdf.inputs["Specular"])
        invert = nodes.new("ShaderNodeInvert")
        invert.location = _POS_GLOSS_INV
        invert.inputs["Fac"].default_value = 0.5
        links.new(tex.outputs["Alpha"], invert.inputs["Color"])
        links.new(invert.outputs["Color"], bsdf.inputs["Roughness"])
        rough_feed = invert.outputs["Color"]

    emission = by_role.get("emission")
    if emission is not None:
        tex = _image_node(mat, emission, vector, _POS_TEX["emission"])
        links.new(tex.outputs["Color"], bsdf.inputs["Emissive Color"])
        emission_feed = tex.outputs["Color"]

    # cycles output from same maps, export ignores links
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = _POS_PRINCIPLED
    out_cycles = nodes.new("ShaderNodeOutputMaterial")
    out_cycles.location = _POS_OUT_CYCLES
    out_cycles.target = "CYCLES"
    if base_feed is not None:
        links.new(base_feed, principled.inputs["Base Color"])
    if rough_feed is not None:
        links.new(rough_feed, principled.inputs["Roughness"])
    if normal_feed is not None:
        links.new(normal_feed, principled.inputs["Normal"])
    if trans_feed is not None:
        links.new(trans_feed, principled.inputs["Alpha"])
    if emission_feed is not None:
        links.new(emission_feed, principled.inputs["Emission Color"])
    links.new(principled.outputs["BSDF"], out_cycles.inputs["Surface"])
    wired = {id(n.image) for n in nodes if n.type == "TEX_IMAGE" and n.image is not None}
    for semantic, image in list(images.items()):
        # unlinked nodes so editor shows unrecognized slots
        if id(image) not in wired:
            _image_node(mat, image)
            wired.add(id(image))
    by_semantic: dict = {}
    for semantic, image in list(images.items()):
        by_semantic.setdefault(id(image), semantic)
    for node in nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            node.label = by_semantic.get(id(node.image), "")


def _tint_group(side: str):
    # fresh dao tint group, one base colour tinted per mask channel

    names = for_side(side)
    tree: Any = bpy.data.node_groups.new(group_name(side), "ShaderNodeTree")
    for name in ("Tint mask (RGB)", side, *names):
        sock = tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketColor")
        sock.default_value = DEFAULTS.get(name, (0.0, 0.0, 0.0, 1.0))
    for name in ("Tint mask (A)", *(alpha_input(n) for n in names if alpha_input(n))):
        tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket("Result", in_out="OUTPUT", socket_type="NodeSocketColor")
    nodes = tree.nodes
    links = tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-1180.0, -25.6)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (500.0, -500.0)
    mask = nodes.new("ShaderNodeSeparateColor")
    mask.location = (-800.0, -620.0)
    mask.label = "mask channels"
    links.new(gin.outputs["Tint mask (RGB)"], mask.inputs["Color"])
    masks = (
        mask.outputs["Red"],
        mask.outputs["Green"],
        mask.outputs["Blue"],
        gin.outputs["Tint mask (A)"],
    )

    opacity = nodes.new("ShaderNodeSeparateColor")
    opacity.location = (-520.0, -60.0)
    opacity.label = f"{side} opacity"
    links.new(gin.outputs[f"{side} opacity"], opacity.inputs["Color"])
    levels_in = (
        opacity.outputs["Red"],
        opacity.outputs["Green"],
        opacity.outputs["Blue"],
        gin.outputs[f"{side} opacity A"],
    )
    feed = gin.outputs[side]
    out = feed

    # channel, x, y, mask
    for (channel, x, y, _), mask_out, level_in in zip(
        (
            ("R", 300.0, 140.0, "Red"),
            ("G", 100.0, -40.0, "Green"),
            ("B", -100.0, -220.0, "Blue"),
            ("A", -300.0, -400.0, None),
        ),
        masks,
        levels_in,
    ):
        level = nodes.new("ShaderNodeMath")
        level.operation = "MULTIPLY"
        level.label = f"{side} {channel}"
        level.location = (x - 240.0, y)
        links.new(mask_out, level.inputs[0])
        links.new(level_in, level.inputs[1])
        mix = nodes.new("ShaderNodeMix")
        mix.blend_type = "MIX"
        mix.data_type = "RGBA"
        mix.label = f"{side} {channel} tint"
        mix.location = (x, y)
        links.new(out, mix.inputs["A"])
        links.new(gin.outputs[f"{side} {channel}"], mix.inputs["B"])
        links.new(level.outputs["Value"], mix.inputs["Factor"])
        out = mix.outputs["Result"]
    links.new(out, gout.inputs["Result"])
    return tree


def _image_node(mat, image, vector=None, pos=None):
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.width = 240.0
    if pos is not None:
        tex.location = pos
    if vector is not None:
        mat.node_tree.links.new(vector, tex.inputs["Vector"])
    return tex


def _to_transparency(mat, bsdf, alpha_out, cutout: bool = False):
    # dao alpha into transparency socket, cutout clips rather than blends
    links = mat.node_tree.links
    invert = mat.node_tree.nodes.new("ShaderNodeInvert")
    invert.location = _POS_TRANS_INV
    links.new(alpha_out, invert.inputs["Color"])
    links.new(invert.outputs["Color"], bsdf.inputs["Transparency"])
    if cutout:
        mat.blend_method = "CLIP"
        mat.alpha_threshold = 0.5
    else:
        mat.blend_method = "BLEND"
    return invert.outputs["Color"]


# transparent semantics blend, others ignore diffuse alpha
_TRANSPARENT_SEMANTICS = ("alpha", "punchthrough", "addvpunchthrough")
# punchthrough semantics are alpha-tested not blended
_CUTOUT_SEMANTICS = ("punchthrough", "addvpunchthrough")


def _transparent_semantic(mao) -> bool:
    semantic = (mao.get("semantic") or "").lower()
    return semantic.startswith(_TRANSPARENT_SEMANTICS)


def _cutout_semantic(mao) -> bool:
    semantic = (mao.get("semantic") or "").lower()
    return semantic.startswith(_CUTOUT_SEMANTICS)


def _channel_varies(image, channel: int) -> bool:
    # true if channel varies, reads via foreach_get into numpy

    try:
        pixels = image.pixels
        count = len(pixels)
    except (AttributeError, TypeError, ReferenceError):
        return False
    if not count:
        return False
    flat = np.empty(count, dtype=np.float32)
    try:
        pixels.foreach_get(flat)
    except (AttributeError, TypeError, ReferenceError):
        return False
    vals = flat[channel::4]
    return bool(vals.size) and float(vals.max()) - float(vals.min()) > 1e-3


def _has_alpha(image) -> bool:
    return _channel_varies(image, 3)


def build_hierarchy(context, tree, chunks):
    # armature from nodes, empties for hooks, skinned chunks on armature

    arm_data = bpy.data.armatures.new(tree.name or "skeleton")
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
                bone.head = loc
                tail = quat_rotate(quat, (0.02, 0.0, 0.0))
                bone.tail = (loc[0] + tail[0], loc[1] + tail[1], loc[2] + tail[2])
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


def build_chunk_object(context, mesh, chunk, raw, source):
    # one blender object per chunk, registers source msh for export

    build = decode_chunk(mesh, chunk)
    # weld co-located rows so edits move surface without cracking seams
    positions, row_to_vert = weld_positions(build["positions"])
    tris = [tuple(row_to_vert[v] for v in tri) for tri in build["triangles"]]

    data = bpy.data.meshes.new(chunk.name or "chunk")
    data.from_pydata(positions, [], tris)

    if build["normals"]:
        data.normals_split_custom_set(
            [build["normals"][v] for tri in build["triangles"] for v in tri]
        )

    for i, (uidx, coords) in enumerate(sorted(build["uvs"].items())):
        layer = data.uv_layers.new(name="UVMap" if i == 0 else f"UVMap.{i:03d}")
        if layer is None:
            continue
        layer.data.foreach_set(
            "uv", [c for tri in build["triangles"] for v in tri for c in coords[v]]
        )
    data.update()
    obj = bpy.data.objects.new(data.name, data)
    context.collection.objects.link(obj)

    if len(positions) != len(build["positions"]):
        obj["thedas_io_row_to_vert"] = row_to_vert

    for bone, members in build["groups"].items():
        group = obj.vertex_groups.new(name=f"bone_{bone}")
        for vidx, weight in members:
            group.add([row_to_vert[vidx]], weight, "REPLACE")
    remember(source, "msh", mesh, raw)["objects"].append((obj, chunk))
    return obj


_CLASSES: tuple = (
    THEDAS_IO_AssetItem,
    THEDAS_IO_ExportItem,
    THEDAS_IO_RenameItem,
    THEDAS_IO_UL_assets,
    THEDAS_IO_UL_export,
    THEDAS_IO_UL_rename,
    THEDAS_IO_PT_assets,
    THEDAS_IO_PT_import,
    THEDAS_IO_PT_export,
    THEDAS_IO_PT_substance,
    THEDAS_IO_AddonPreferences,
    THEDAS_IO_OT_scan_assets,
    THEDAS_IO_OT_refresh_assets,
    THEDAS_IO_OT_import_asset,
    THEDAS_IO_OT_import_file,
    THEDAS_IO_OT_export_assets,
    THEDAS_IO_OT_check_model,
    THEDAS_IO_OT_reload_textures,
    THEDAS_IO_OT_export_substance,
    THEDAS_IO_OT_import_substance,
)
_STRING_PROPS = ("thedas_io_filter_name", "thedas_io_filter_package", "thedas_io_db_path")
_DIR_PROPS = ("thedas_io_export_dir", "thedas_io_sp_dir")
# drawn inputs need name and tooltip, text at draw only relabels
_PROP_TEXT = {
    "thedas_io_filter_name": ("File name", "Show only assets whose file name contains this text"),
    "thedas_io_filter_package": (
        "Package",
        (
            "Show only assets from this archive file, e.g. "
            "modelmeshdata.erf; blank searches every package"
        ),
    ),
    "thedas_io_db_path": (
        "Database",
        (
            "Asset database file override; blank uses the default "
            "location under the application data folder"
        ),
    ),
    "thedas_io_export_dir": (
        "Export to",
        (
            "Standalone files land here on Export; untouched "
            "resources come out byte-identical to the source"
        ),
    ),
    "thedas_io_sp_dir": (
        "Folder",
        (
            "The Substance set lives here: model.obj, model.mtl, "
            "textures/ and meshmaps/. Export writes it, Import reads "
            "the painted maps back from it"
        ),
    ),
}
# combo box beats free text, only msh/mmh have importers
_EXT_ITEMS = (
    ("NONE", "(None)", "Every extension"),
    ("MSH", "MSH", "Mesh (.msh) only"),
    ("MMH", "MMH", "Model hierarchy (.mmh) only"),
)
_SCENE_PROPS = (
    "thedas_io_assets",
    "thedas_io_index",
    "thedas_io_export_items",
    "thedas_io_export_index",
    "thedas_io_rename_items",
    "thedas_io_rename_index",
    "thedas_io_scan_progress",
    "thedas_io_scan_active",
    *_STRING_PROPS,
    *_DIR_PROPS,
    "thedas_io_filter_ext",
    "thedas_io_export_tint",
    "thedas_io_tint_name",
    "thedas_io_rename_export",
    "thedas_io_import_source",
    "thedas_io_registry",
    "thedas_io_disk_file",
    "thedas_io_disk_local",
    "thedas_io_sp_split_packed",
    "thedas_io_sp_keep_resolution",
)


def register():
    global _LOAD_HANDLER, _PRUNE_HANDLER
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.thedas_io_assets = bpy.props.CollectionProperty(type=THEDAS_IO_AssetItem)  # type: ignore
    bpy.types.Scene.thedas_io_index = bpy.props.IntProperty()  # type: ignore
    bpy.types.Scene.thedas_io_export_items = bpy.props.CollectionProperty(type=THEDAS_IO_ExportItem)  # type: ignore
    bpy.types.Scene.thedas_io_export_index = bpy.props.IntProperty()  # type: ignore
    bpy.types.Scene.thedas_io_rename_items = bpy.props.CollectionProperty(type=THEDAS_IO_RenameItem)  # type: ignore
    bpy.types.Scene.thedas_io_rename_index = bpy.props.IntProperty()  # type: ignore

    bpy.types.Scene.thedas_io_scan_progress = bpy.props.IntProperty(  # type: ignore
        min=0, max=100, default=0, description="Archive scan progress in percent"
    )
    bpy.types.Scene.thedas_io_scan_active = bpy.props.BoolProperty(default=False)  # type: ignore

    bpy.types.Scene.thedas_io_export_tint = bpy.props.BoolProperty(  # type: ignore
        default=False,
        description="Write each model's DAO Tint values as a .tnt for the "
        "chargen variation path, bake them into its MAO as "
        "mml_vTintMaskColours as a fallback, and clear the "
        "mesh's USE_VARIATION_TINT flag",
    )
    bpy.types.Scene.thedas_io_tint_name = bpy.props.StringProperty(  # type: ignore
        name="Tint resource",
        description="Name of the .tnt to write, e.g. t3_arm_rlr.tnt. No MMH "
        "field points at a .tnt: the engine requests the name "
        "the equipped item's tint variation uses (chargen-style), "
        "and a loose file in the override dir shadows the stock "
        "one. Leave blank for <model>.tnt",
    )
    bpy.types.Scene.thedas_io_rename_export = bpy.props.BoolProperty(  # type: ignore
        default=False, description="Show per-file rename fields; set names apply on export"
    )

    bpy.types.Scene.thedas_io_sp_split_packed = bpy.props.BoolProperty(  # type: ignore
        default=True,
        description="Split packed hair-style maps into basecolor/specular/"
        "opacity for Substance; off keeps the map whole so "
        "nothing is silently lossy",
    )
    bpy.types.Scene.thedas_io_sp_keep_resolution = bpy.props.BoolProperty(  # type: ignore
        default=False,
        description="Keep the painted resolution on Substance import "
        "instead of resampling to the source size. The "
        "exported DDS keeps the Substance size; the game "
        "build pipeline caps textures per category, so "
        "oversized maps are your responsibility",
    )

    for prop in _STRING_PROPS:
        name, description = _PROP_TEXT[prop]
        setattr(bpy.types.Scene, prop, bpy.props.StringProperty(name=name, description=description))
    for prop in _DIR_PROPS:
        name, description = _PROP_TEXT[prop]
        setattr(
            bpy.types.Scene,
            prop,
            bpy.props.StringProperty(name=name, subtype="DIR_PATH", description=description),
        )

    bpy.types.Scene.thedas_io_import_source = bpy.props.EnumProperty(  # type: ignore
        name="Import source",
        description="Browse the scanned game database, or import one model file straight from disk",
        items=[
            ("GAME", "Game assets", "Search the asset database"),
            ("DISK", "Disk", "A .msh/.mmh file from the filesystem"),
        ],
        default="GAME",
    )
    bpy.types.Scene.thedas_io_registry = bpy.props.StringProperty(  # type: ignore
        name="Registry",
        description="JSON record of imported resources, so reopening "
        "the file can restore the export registry",
    )
    bpy.types.Scene.thedas_io_disk_file = bpy.props.StringProperty(  # type: ignore
        name="File", subtype="FILE_PATH", description="Model file to import (.msh or .mmh)"
    )
    bpy.types.Scene.thedas_io_disk_local = bpy.props.BoolProperty(  # type: ignore
        default=True,
        description="Resolve the MAO, textures and sibling mesh from "
        "files next to the imported file, never the game",
    )
    bpy.types.Scene.thedas_io_filter_ext = bpy.props.EnumProperty(  # type: ignore
        name="Extension",
        items=_EXT_ITEMS,
        default="NONE",
        description="List only models and hierarchies, or every indexed extension",
    )

    if _LOAD_HANDLER is None:
        _LOAD_HANDLER = bpy.app.handlers.persistent(_on_file_load)
    handlers = bpy.app.handlers.load_post
    if _LOAD_HANDLER not in handlers:
        handlers.append(_LOAD_HANDLER)
    if _PRUNE_HANDLER is None:
        _PRUNE_HANDLER = bpy.app.handlers.persistent(_on_depsgraph_update)
    updates = bpy.app.handlers.depsgraph_update_post
    if _PRUNE_HANDLER not in updates:
        updates.append(_PRUNE_HANDLER)
    saves = bpy.app.handlers.save_post
    if _relocate_cache not in saves:
        saves.append(bpy.app.handlers.persistent(_relocate_cache))
    pres = bpy.app.handlers.save_pre
    if _repack_textures not in pres:
        pres.append(bpy.app.handlers.persistent(_repack_textures))


_LOAD_HANDLER = None
_PRUNE_HANDLER = None


def registry_blob() -> str:
    # json descriptor of imported resources, names only never bytes

    out = []

    for key, item in ITEMS.items():
        names = []
        for entry in item.get("objects") or []:
            obj = entry[0] if isinstance(entry, (tuple, list)) else entry
            try:
                names.append(obj.name)
            except (AttributeError, ReferenceError):
                pass
        out.append(
            {
                "f": key,
                "k": item.get("kind"),
                "objs": names,
                "arm": _safe_name(item.get("armature")),
                "meshes": [_safe_name(o) for o in item.get("meshes") or []],
            }
        )
    return json.dumps(out)


def _safe_name(obj) -> str:
    try:
        return obj.name if obj is not None else ""
    except (AttributeError, ReferenceError):
        return ""


def _store_registry(scene) -> None:
    try:
        scene.thedas_io_registry = registry_blob()
    except (AttributeError, TypeError):
        pass


def rebuild_registry(scene) -> list:
    # re-populate import registry after blend is opened

    try:
        blob = scene.thedas_io_registry
    except AttributeError:
        return []
    if not blob:
        return []
    try:
        entries = json.loads(blob)
    except ValueError:
        return []
    db = getattr(scene, "thedas_io_db_path", "") or None
    notes: list = []

    def fetch(name):
        row = {"package": None, "filename": name, "ext": Path(name).suffix, "path": ""}
        try:
            return resolve(name, row, db, False)
        except (OSError, ValueError, KeyError, sqlite3.Error):
            return None

    meshes_by_name = {}
    for obj in bpy.context.scene.objects if bpy.context.scene else []:
        meshes_by_name[obj.name] = obj
    images_by_name = {img.name: img for img in bpy.data.images}
    mats_by_name = {mat.name: mat for mat in bpy.data.materials}

    for entry in entries:
        name, kind = entry.get("f"), entry.get("k")
        if not name or not kind:
            continue
        path = fetch(name)
        if path is None:
            notes.append(f"{name}: not found in the asset database")
            continue
        raw = path.read_bytes()
        item = remember(name, kind, None, raw)
        item["objects"] = []
        if kind == "mmh":
            item["model"] = MMHReader(raw).tree
            item["armature"] = bpy.data.objects.get(entry.get("arm") or "")
            item["meshes"] = [
                meshes_by_name[n] for n in entry.get("meshes") or [] if n in meshes_by_name
            ]
        elif kind == "msh":
            # msh item carries object chunk pairs, match chunk back by name
            mesh = MSHReader(raw).mesh
            item["model"] = mesh
            by_chunk = {c.name: c for c in mesh.chunks}  # type: ignore
            pairs = []
            for obj_name in entry.get("objs") or []:
                obj = meshes_by_name.get(obj_name)
                chunk = by_chunk.get(obj_name)
                if obj is not None and chunk is not None:
                    pairs.append((obj, chunk))
            item["objects"] = pairs
        elif kind == "mao":
            item["model"] = parse_mao(raw.decode("utf-8"))
            item["objects"] = [
                m for m in (mats_by_name.get(n) for n in entry.get("objs") or []) if m is not None
            ]
        else:
            item["objects"] = [
                i for i in (images_by_name.get(n) for n in entry.get("objs") or []) if i is not None
            ]
    return notes


def _autoload_db():
    # fill empty asset lists from saved database on file load
    scenes = getattr(getattr(bpy, "data", None), "scenes", None) or []
    for scene in list(scenes):
        try:
            empty = len(scene.thedas_io_assets) == 0
        except (AttributeError, TypeError):
            continue
        if not empty:
            continue

        try:
            _fill_assets(scene, getattr(scene, "thedas_io_db_path", "") or None)
        except (AttributeError, OSError, KeyError, sqlite3.Error) as e:
            print(f"DAO: asset list refresh failed: {e}")


def _on_file_load(_dummy):
    _autoload_db()
    # import registry is python-side and does not survive save

    for scene in list(getattr(getattr(bpy, "data", None), "scenes", None) or []):
        try:
            for note in rebuild_registry(scene):
                print(f"DAO: {note}")
        except (AttributeError, KeyError, ValueError) as e:
            print(f"DAO: registry rebuild failed: {e}")
            continue

    # old blend may carry free-text value
    known = {item[0] for item in _EXT_ITEMS}
    for scene in list(getattr(getattr(bpy, "data", None), "scenes", None) or []):
        if getattr(scene, "thedas_io_filter_ext", "NONE") not in known:
            scene.thedas_io_filter_ext = "NONE"
        # drop stale scan flag so future scans run
        try:
            scene.thedas_io_scan_active = False
            scene.thedas_io_scan_progress = 0
        except (AttributeError, ReferenceError, RuntimeError):
            continue


def unregister():
    global _LOAD_HANDLER, _PRUNE_HANDLER

    for prop in _SCENE_PROPS:
        delattr(bpy.types.Scene, prop)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    reset()  # source bytes and blender members of old session go now

    try:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    except ValueError:
        pass
    try:
        bpy.app.handlers.save_post.remove(_relocate_cache)
    except ValueError:
        pass
    try:
        bpy.app.handlers.save_pre.remove(_repack_textures)
    except ValueError:
        pass
    _PRUNE_HANDLER = None
    if _LOAD_HANDLER is not None:
        try:
            bpy.app.handlers.load_post.remove(_LOAD_HANDLER)
        except ValueError:
            pass
        _LOAD_HANDLER = None
