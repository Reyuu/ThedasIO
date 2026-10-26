# blender ui panels, lists and operators

import io
import json
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

import bpy
from PIL import Image as PilImage

from .ani.operators import (
    THEDAS_IO_OT_export_animation,
    THEDAS_IO_OT_load_animation,
    _play_animation,
)
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
from .blender_materials import (
    apply_materials,
    build_material,  # noqa: F401 - public helper for tests and scripts
)
from .builders import build_chunk_object, build_hierarchy
from .datablocks import sweep_orphans as _sweep_orphans
from .export import (
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
from .fsio import save_image_atomic, write_text_atomic
from .geometry import (
    decode_chunk,
)
from .materials import (
    parse_mao_bytes,
)
from .mmh.description import MMHReader
from .msh.description import MSHReader
from .phy.operators import THEDAS_IO_OT_add_collision, _load_collision
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
    _relocate_cache,
    _scale_live_images,
    _texture_images,
    load_image,  # noqa: F401 - public helper for tests and scripts
    reload_textures,
)


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
