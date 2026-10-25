# blender ui panels, lists and operators

import base64
import io
import json
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any, ClassVar

import bpy
import numpy as np
from PIL import Image as PilImage

from .asset_browser import (
    THEDAS_IO_OT_assets_page,
    THEDAS_IO_OT_refresh_assets,
    THEDAS_IO_OT_scan_assets,
    _asset_limit,
    _fill_assets,
    _invalidate_asset_limit,
    _page_count,
    _scene_db,
)
from .asset_db.scanner import _db_path, find_exact
from .assets import cleanup_session, extract, importer_for, resolve
from .builders import build_chunk_object, build_hierarchy
from .datablocks import sweep_orphans as _sweep_orphans
from .datablocks import tag as _tag
from .export import (
    _LOADED_ITEMS,
    ITEMS,
    Item,
    _tnt_name,
    check_model,
    connected,
    export_all,
    phy_enabled,
    remember,
    reset,
    roots,
)
from .fsio import save_image_atomic, write_bytes_atomic, write_text_atomic
from .geometry import (
    decode_chunk,
)
from .materials import (
    parse_mao_bytes,
    slot_roles,
)
from .mmh.description import MMHReader
from .msh.description import MSHReader
from .phy.description import PhyReader
from .phy.scene import build_proxies, default_name, mark_snapshots
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
from .textures import (
    _cache_dir,
    _existing_tint,
    _missing_image,
    _relocate_cache,
    _resolve_texture,
    _scale_live_images,
    _texture_images,
    load_image,  # noqa: F401 - public helper for tests and scripts
    reload_textures,
)
from .tnt import DEFAULTS, alpha_input, for_side, group_name


class THEDAS_IO_AssetItem(bpy.types.PropertyGroup):
    package: bpy.props.StringProperty()  # type: ignore
    filename: bpy.props.StringProperty()  # type: ignore
    ext: bpy.props.StringProperty()  # type: ignore
    path: bpy.props.StringProperty()  # type: ignore
    # DA2 entries are named by hash only, so extraction needs it back.
    # 64-bit values do not fit a bpy IntProperty, so keep it as hex text.
    name_hash: bpy.props.StringProperty()  # type: ignore


def _viewport_objects(item: Item) -> list:
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
    _sync_rename_later()


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
        self.layout.prop(scene, "thedas_io_game", text="Game")
        self.layout.prop(scene, "thedas_io_import_source", expand=True)
        self.layout.prop(scene, "thedas_io_weld", text="Weld seams")
        if scene.thedas_io_import_source == "GAME":
            self.layout.prop(scene, "thedas_io_filter_ext", text="Extension")
            self.layout.prop(scene, "thedas_io_filter_name", text="File name")
            self.layout.prop(scene, "thedas_io_filter_package", text="Package")
            self.layout.operator("thedas_io.refresh_assets", text="Search")
            row = self.layout.row(align=True)
            row.prop(scene, "thedas_io_scan_addins", text="Include AddIns")
            row.operator("thedas_io.scan_assets", text="Scan")
            if scene.thedas_io_scan_addins:
                self.layout.prop(scene, "thedas_io_addins_dir", text="")
            if phy_enabled(context):
                arm = getattr(context, "active_object", None)
                if arm is None or arm.type != "ARMATURE":
                    arms = [o for o in getattr(scene, "objects", []) if o.type == "ARMATURE"]
                    arm = arms[-1] if arms else None
                if arm is not None and hasattr(arm, "data"):
                    self.layout.prop_search(
                        scene, "thedas_io_collision_bone", arm.data, "bones", text="Bone"
                    )
                row2 = self.layout.row(align=True)
                row2.operator("thedas_io.add_collision", text="Add Collision")
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

            limit = _asset_limit(context)
            total = getattr(scene, "thedas_io_assets_total", len(scene.thedas_io_assets))
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(scene.thedas_io_assets)
            if limit is not None and total > limit:
                pages = _page_count(total, limit)
                page = getattr(scene, "thedas_io_page", 0) or 0
                page = max(0, min(int(page), pages - 1))
                row = self.layout.row(align=True)
                prev = row.operator("thedas_io.assets_page", text="Prev")
                prev.step = -1
                row.label(text=f"Page {page + 1}/{pages} ({total})")
                nxt = row.operator("thedas_io.assets_page", text="Next")
                nxt.step = 1

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
        anim_row = self.layout.row(align=True)
        anim_row.operator("thedas_io.export_animation", text="Export Animation")
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
    # checked roots. None means "no object list at all, export everything";
    # a list that exists but has nothing ticked resolves to an empty set, so a
    # caller can tell "all" from "none" instead of exporting everything
    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return None
    if not rows:
        return None
    return connected({r.filename for r in rows if getattr(r, "use", True)})


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
        folder = self.directory or getattr(scene, "thedas_io_sp_dir", "")
        if not folder:
            # Path("") is Path("."), so an empty folder used to pass the old
            # guard and write model.obj into whatever Blender's cwd happened
            # to be
            self.report({"WARNING"}, "Pick a Substance folder first")
            return {"CANCELLED"}
        dest = Path(bpy.path.abspath(folder))

        scene.thedas_io_sp_dir = str(dest)
        keys = _checked_keys(scene)
        wanted = [k for k in ITEMS if keys is None or k in keys]
        if keys is not None and not wanted:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}
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
        write_text_atomic(dest / "model.obj", obj_text([(n, m, d) for n, d, m in chunks]))
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
                save_image_atomic(pil, path, "PNG")
                written.append(str(path.relative_to(dest)))
            mtl_maps[mat_name] = set(maps)

            # meshmap copy for substance auto-assign, do not rebake in substance
            if MAP_NORMAL in maps:
                meshmap_dir = dest / "meshmaps"
                meshmap_dir.mkdir(parents=True, exist_ok=True)
                meshmap = meshmap_dir / f"{mat_name}_normal_base.png"
                save_image_atomic(maps[MAP_NORMAL], meshmap, "PNG")
                written.append(str(meshmap.relative_to(dest)))

        write_text_atomic(dest / "model.mtl", mtl_text([(m, mtl_maps.get(m, ())) for m in maos]))
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
                    save_image_atomic(pil, folder / Path(res).name, "PNG")
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


def _root_alive(item: Item) -> bool:
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


_SYNCING_RENAMES = False


def _sync_rename_items(scene) -> None:
    # rename table lists exported files of checked roots

    # rebuilding the collection mutates data the UI list is bound to, so it must
    # never re-enter
    global _SYNCING_RENAMES
    if _SYNCING_RENAMES:
        return
    _SYNCING_RENAMES = True
    try:
        _fill_rename_items(scene)
    finally:
        _SYNCING_RENAMES = False


_SYNC_PENDING = False


def _sync_rename_later() -> None:
    global _SYNC_PENDING
    if _SYNC_PENDING:
        return
    _SYNC_PENDING = True
    try:
        bpy.app.timers.register(_sync_rename_timer, first_interval=0.2)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        _SYNC_PENDING = False


def _sync_rename_timer():
    # falling off the end returns None, which stops the one-shot timer
    global _SYNC_PENDING
    _SYNC_PENDING = False
    try:
        for one in bpy.data.scenes:
            _sync_rename_items(one)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _fill_rename_items(scene) -> None:
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

    for r in list(rows):
        name = getattr(r, "filename", "")
        new = (getattr(r, "export_name", "") or "").strip()
        hit = ITEMS.get(name)
        if hit is not None:
            hit["as"] = new
        elif name.lower() in tnt_of:
            # renamed tint belongs to its mmh, row wins over tint_name
            mmh = ITEMS[tnt_of[name.lower()]]
            mmh["as_tnt"] = new if not new or new.lower().endswith(".tnt") else f"{new}.tnt"


_PRUNE_PENDING = False
_SWEEPING = False


def _stale_export_rows(scene) -> bool:
    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return False
    for row in rows:
        item = ITEMS.get(row.filename)
        if item is None or not _root_alive(item):
            return True
    return False


def _prune_later() -> None:
    global _PRUNE_PENDING
    if _PRUNE_PENDING:
        return
    _PRUNE_PENDING = True
    try:
        bpy.app.timers.register(_prune_timer, first_interval=0.2)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        _PRUNE_PENDING = False


def _prune_timer():
    # falling off the end returns None, which stops the one-shot timer
    global _PRUNE_PENDING
    _PRUNE_PENDING = False
    pruned = False
    try:
        for one in bpy.data.scenes:
            if _prune_export_items(one):
                pruned = True
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    if pruned:
        _sweep_later()


def _sweep_later() -> None:
    # datablocks must not be removed from inside a depsgraph handler, so defer
    # to a one-shot timer that runs between evaluations
    global _SWEEPING
    if _SWEEPING:
        return
    _SWEEPING = True
    try:
        bpy.app.timers.register(_sweep_timer, first_interval=0.2)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        _SWEEPING = False


def _sweep_timer():
    # falling off the end returns None, which stops the one-shot timer
    global _SWEEPING
    _SWEEPING = False
    try:
        _sweep_orphans()
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _on_depsgraph_update(scene, depsgraph=None) -> None:
    # keep the object list honest on viewport deletes. nothing is removed here:
    # mutating scene data inside a depsgraph handler is the crash class this
    # function used to hit, so a stale row only schedules the prune timer
    if not ITEMS:
        return

    try:
        stale = any(_stale_export_rows(one) for one in bpy.data.scenes)
    except (AttributeError, ReferenceError, RuntimeError):
        return  # mid-teardown scene data; the next update retries
    if stale:
        _prune_later()


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
    thedas_io_da2_game_dir: bpy.props.StringProperty(  # type: ignore
        name="Dragon Age II folder",
        subtype="DIR_PATH",
        default="",
        description="Dragon Age II install folder; Scan indexes it when no folder is given",
    )
    thedas_io_enable_phy: bpy.props.BoolProperty(  # type: ignore
        name="Enable PHY collision",
        default=False,
        description="Enable experimental PHY collision support: importing "
        "collision proxies, the Add Collision operator and collision export. "
        "Off keeps PHY parsing read-only in the background.",
    )
    thedas_io_unlimited_assets: bpy.props.BoolProperty(  # type: ignore
        name="Show all assets (no limit)",
        default=False,
        update=_invalidate_asset_limit,
        description="Load every database row into the asset list, as before. "
        "Needs lots of memory with a full game install (10k-100k rows); "
        "leave off and use search instead",
    )

    def draw(self, context):
        if self.layout is None:
            return
        self.layout.prop(self, "thedas_io_game_dir")
        self.layout.prop(self, "thedas_io_da2_game_dir")
        self.layout.prop(self, "thedas_io_enable_phy")
        self.layout.prop(self, "thedas_io_unlimited_assets")


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
        from .export import phy_enabled

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
        from .export import phy_enabled

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
    from .export import ITEMS

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
    from .phy.scene import add_proxy_for

    obj = add_proxy_for(context, spec, arm, bone)
    item.setdefault("collision", []).append(obj)
    mark_snapshots([obj])
    return obj.name


def _play_animation(context, raw: bytes, name: str, fps: float, emit) -> set:
    from .ani.description import ANIReader
    from .ani.scene import _fcurves, build_action

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

    def execute(self, context) -> set:
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

    def invoke(self, context, event) -> set:
        action = self._action(context)
        if action is None:
            return {"CANCELLED"}
        self.filepath = f"{action.name}.ani"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _action(self, context):
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

    def execute(self, context) -> set:
        from .ani.export import export_action

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


class THEDAS_IO_OT_import_asset(bpy.types.Operator):
    bl_idname = "thedas_io.import_asset"
    bl_label = "Import DAO asset"
    bl_description = (
        "Import the selected asset: a model with its sibling mesh, "
        "material and textures resolved from the database, "
        "or a .ani played on the active armature"
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
            "name_hash": item.name_hash or None,
        }
        if str(item.ext).lower().lstrip(".") == "ani":
            try:
                raw = Path(str(extract(row))).read_bytes()
            except Exception as exc:  # noqa: BLE001
                self.report({"ERROR"}, f"Could not extract {item.filename}: {exc}")
                return {"CANCELLED"}
            return _play_animation(context, raw, item.filename, 30.0, self.report)
        result = _import_row(
            context, row, _scene_db(scene), False, self.report, scene.thedas_io_weld
        )

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result


class THEDAS_IO_OT_import_file(bpy.types.Operator):
    bl_idname = "thedas_io.import_file"
    bl_label = "Import DAO file"
    bl_description = (
        "Import one file from disk: a .msh/.mmh model, resolving its "
        "siblings next to the file instead of from the game, "
        "or a .ani played on the active armature"
    )

    def execute(self, context) -> set:
        scene = context.scene
        path = bpy.path.abspath(scene.thedas_io_disk_file or "")
        if not path or not Path(path).is_file():
            self.report({"WARNING"}, "Pick a file first")
            return {"CANCELLED"}
        p = Path(path)
        if p.suffix.lower() == ".ani":
            try:
                raw = p.read_bytes()
            except OSError as exc:
                self.report({"ERROR"}, f"Could not read {p.name}: {exc}")
                return {"CANCELLED"}
            return _play_animation(context, raw, p.name, 30.0, self.report)
        row = {"package": None, "filename": p.name, "ext": p.suffix, "path": str(p)}
        result = _import_row(
            context,
            row,
            None,
            scene.thedas_io_disk_local,
            self.report,
            scene.thedas_io_weld,
        )

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result


def _import_row(context, row: dict, db, local_only: bool, emit, weld: bool = True) -> set:
    # shared msh/mmh import for one row, emit reports to user
    game = getattr(context.scene, "thedas_io_game", "DAO")
    # a blank override must still mean this game's default db, not DAO's
    db = _db_path(db, game)

    if not row.get("name_hash"):
        hit = find_exact(row["filename"], db_path=db, game=game)
        if hit is not None and hit["path"] == row["path"]:
            row = {**row, "name_hash": hit["name_hash"]}

    local = extract(row)
    kind = importer_for(row["ext"])
    if kind == "msh":
        raw = local.read_bytes()
        mesh = MSHReader(raw).mesh
        objs = [
            build_chunk_object(context, mesh, chunk, raw, row["filename"], weld)
            for chunk in mesh.chunks  # type: ignore
        ]
        notes: list = []
        stem = Path(row["filename"]).stem
        mao = resolve(f"{stem}.mao", row, db, local_only, game=game)
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
        sibling = resolve(msh_name, row, db, local_only, game=game)
        if sibling is not None and sibling.is_file():
            sraw = sibling.read_bytes()
            smesh = MSHReader(sraw).mesh
            for chunk in smesh.chunks:  # type: ignore
                chunks[chunk.name] = build_chunk_object(context, smesh, chunk, sraw, msh_name, weld)

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
        collisions = _load_collision(
            context, row, stem, reg, stats["armature"], db, local_only, emit
        )
        for warning in dict.fromkeys(notes):
            emit({"WARNING"}, warning)
        col = f", {len(collisions)} collision" if collisions else ""
        emit(
            {"INFO"},
            f"Imported {tree.name}: "  # type: ignore
            f"{stats['bones']} bones, {stats['empties']} empties, "
            f"{len(stats['linked'])} meshes{col}{note}",
        )
        return {"FINISHED"}
    emit({"WARNING"}, f"No importer for {row['filename']}")
    return {"CANCELLED"}


def _load_collision(context, row, stem, reg, arm_obj, db, local_only, emit) -> list:
    # a .phy beside the .mmh becomes plain proxy objects on the bones it names,
    # only while experimental PHY support is switched on
    from .export import phy_enabled

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
        parsed = parse_mao_bytes(mao_bytes)

        item = remember(mao_path.name, "mao", parsed, mao_bytes)
    else:
        mao_bytes = mao_path.read_bytes()
        parsed = parse_mao_bytes(mao_bytes)
        item = remember(mao_path.name, "mao", parsed, mao_bytes)

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


def _repack_textures(scene=None, depsgraph=None) -> None:
    # keep packed pixels equal to screen before save
    _sweep_orphans()  # dropped imports must not ride along in the .blend
    for image, _ in _texture_images():
        try:
            # pack() re-encodes every pixel; only a dirty image needs it, so an
            # untouched scene saves without re-packing every texture on each save
            if image.packed_file is not None and getattr(image, "is_dirty", True):
                image.pack()
        except (AttributeError, ReferenceError, RuntimeError):
            continue

    for one in bpy.data.scenes:
        _store_registry(one)


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


def build_material(name, mao, images, color=None, tint=None):
    # specular-bsdf wiring from dao channel packing
    mat: Any = bpy.data.materials.new(name)
    _tag(mat, "material")
    mat.use_nodes = True
    _wire_material(mat, mao, images, color=color, tint=tint)

    for key, values in (mao.get("vectors") or {}).items():
        mat[key] = list(values)
    return mat


# fixed slots keep built materials arranged the same way
_POS_UV = (-220.6, 72.9)
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
    # no mapping node: the image is stored the right way up (see _blit), so the
    # raw UV renders correctly *and* texture paint lands where the preview shows
    # it. a scale of (1,-1,1) here un-flipped the render but paint ignores it.
    uvmap = nodes.new("ShaderNodeTexCoord")
    uvmap.location = _POS_UV
    vector = uvmap.outputs["UV"]
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
    # One shared group per side, built once. Creating one per material made
    # Blender uniquify the name ("DAO Tint Diffuse.001") for every material
    # after the first, and the exporter finds the group by exact name, so those
    # materials exported with no tint at all.
    name = group_name(side)
    tree: Any = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree

    names = for_side(side)
    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
    _tag(tree, "node_group")
    for name in ("Tint mask (RGB)", side, *names):
        sock = tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketColor")
        sock.default_value = DEFAULTS.get(name, (0.0, 0.0, 0.0, 1.0))
    alpha_names = [a for n in names if (a := alpha_input(n)) is not None]
    for name in ("Tint mask (A)", *alpha_names):
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
    THEDAS_IO_OT_add_collision,
    THEDAS_IO_OT_load_animation,
    THEDAS_IO_OT_export_animation,
    THEDAS_IO_OT_refresh_assets,
    THEDAS_IO_OT_assets_page,
    THEDAS_IO_OT_import_asset,
    THEDAS_IO_OT_import_file,
    THEDAS_IO_OT_export_assets,
    THEDAS_IO_OT_check_model,
    THEDAS_IO_OT_reload_textures,
    THEDAS_IO_OT_export_substance,
    THEDAS_IO_OT_import_substance,
)
_STRING_PROPS = (
    "thedas_io_filter_name",
    "thedas_io_filter_package",
    "thedas_io_db_path",
    "thedas_io_addins_dir",
)
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
    "thedas_io_addins_dir": (
        "AddIns folder",
        (
            "Where the installer drops DLC and mods; scanned "
            "alongside the game folder when Include AddIns is on. "
            "Blank uses Documents/BioWare/Dragon Age/AddIns"
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
    ("ANI", "ANI", "Animation (.ani) only"),
    ("CUB", "CUB", "Cutscene blend tree (.cub) only"),
)
_SCENE_PROPS = (
    "thedas_io_assets",
    "thedas_io_index",
    "thedas_io_page",
    "thedas_io_assets_total",
    "thedas_io_export_items",
    "thedas_io_export_index",
    "thedas_io_rename_items",
    "thedas_io_rename_index",
    "thedas_io_scan_progress",
    "thedas_io_scan_active",
    "thedas_io_scan_addins",
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
    "thedas_io_weld",
    "thedas_io_collision_bone",
    "thedas_io_sp_split_packed",
    "thedas_io_sp_keep_resolution",
    "thedas_io_game",
)


def register():
    global _LOAD_HANDLER, _PRUNE_HANDLER
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.thedas_io_assets = bpy.props.CollectionProperty(type=THEDAS_IO_AssetItem)  # type: ignore
    bpy.types.Scene.thedas_io_index = bpy.props.IntProperty()  # type: ignore
    bpy.types.Scene.thedas_io_page = bpy.props.IntProperty(  # type: ignore
        default=0, min=0, description="Current page of the asset list"
    )
    bpy.types.Scene.thedas_io_assets_total = bpy.props.IntProperty(  # type: ignore
        default=0, min=0, description="Total assets matching the current filters"
    )
    bpy.types.Scene.thedas_io_export_items = bpy.props.CollectionProperty(type=THEDAS_IO_ExportItem)  # type: ignore
    bpy.types.Scene.thedas_io_export_index = bpy.props.IntProperty()  # type: ignore
    bpy.types.Scene.thedas_io_rename_items = bpy.props.CollectionProperty(type=THEDAS_IO_RenameItem)  # type: ignore
    bpy.types.Scene.thedas_io_rename_index = bpy.props.IntProperty()  # type: ignore

    bpy.types.Scene.thedas_io_scan_progress = bpy.props.IntProperty(  # type: ignore
        min=0, max=100, default=0, description="Archive scan progress in percent"
    )
    bpy.types.Scene.thedas_io_scan_active = bpy.props.BoolProperty(default=False)  # type: ignore
    bpy.types.Scene.thedas_io_scan_addins = bpy.props.BoolProperty(  # type: ignore
        default=False,
        description="Scan the AddIns folder alongside the game folder, so DLC "
        "and mod assets resolve too",
    )

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
    bpy.types.Scene.thedas_io_game = bpy.props.EnumProperty(  # type: ignore
        name="Game",
        description="Which game's assets this scene targets",
        items=[
            ("DAO", "Dragon Age: Origins", "Dragon Age: Origins"),
            ("DA2", "Dragon Age II", "Dragon Age II"),
        ],
        default="DAO",
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
    bpy.types.Scene.thedas_io_weld = bpy.props.BoolProperty(  # type: ignore
        default=True,
        description="Weld rows that sit at the same position into one "
        "vertex, so editing the surface does not crack the seams. "
        "Off keeps every MSH row separate, which is what you "
        "want when re-UVing or when the original split matters",
    )
    # which bone an added collision hangs off. Empty means "whatever is
    # selected", which is what the operator already did; setting it pins the
    # choice so it survives the redo panel and a click with nothing selected
    bpy.types.Scene.thedas_io_collision_bone = bpy.props.StringProperty(  # type: ignore
        name="Bone",
        description="Bone the added collision shape hangs off",
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
    db = _scene_db(scene)
    notes: list = []

    def fetch(name):
        row = {"package": None, "filename": name, "ext": Path(name).suffix, "path": ""}
        try:
            return resolve(name, row, db, False)
        except (OSError, ValueError, KeyError, sqlite3.Error):
            return None

    meshes_by_name: dict[str, Any] = {}
    # the scene the registry belongs to, never the active one: rebuild_registry
    # runs per scene on file load, and an object moved to another scene would
    # otherwise be missing from its own registry
    for scene_obj in getattr(scene, "objects", None) or []:
        meshes_by_name[scene_obj.name] = scene_obj
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
                obj: Any = meshes_by_name.get(obj_name)
                chunk = by_chunk.get(obj_name)
                if obj is not None and chunk is not None:
                    pairs.append((obj, chunk))
            item["objects"] = pairs
        elif kind == "mao":
            item["model"] = parse_mao_bytes(raw)
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
    # import registry is python-side and does not survive save. reset() first:
    # without it the previous file's items keep their source bytes (and dead
    # object refs) alive for the rest of the session, so every open grows memory
    reset()

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
    cleanup_session()  # extracted temporary sources go with them

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
