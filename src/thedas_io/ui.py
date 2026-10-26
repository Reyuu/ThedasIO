# blender ui panels, lists and operators

import sqlite3
from typing import ClassVar

import bpy

from .ani.operators import (
    THEDAS_IO_OT_export_animation,
    THEDAS_IO_OT_load_animation,
)
from .asset_browser import (
    THEDAS_IO_OT_assets_page,
    THEDAS_IO_OT_refresh_assets,
    THEDAS_IO_OT_scan_assets,
    _asset_limit,
    _fill_assets,
    _invalidate_asset_limit,
    _page_count,
)
from .assets import cleanup_session
from .blender_materials import (
    apply_materials,  # noqa: F401 - public helper for tests and scripts
    build_material,  # noqa: F401 - public helper for tests and scripts
)
from .builders import (  # noqa: F401 - public helpers for tests and scripts
    build_chunk_object,
    build_hierarchy,
)
from .datablocks import sweep_orphans as _sweep_orphans
from .export import (
    ITEMS,
    connected,
    phy_enabled,
    reset,
)
from .export_registry import (
    _on_depsgraph_update,
    _prune_export_items,  # noqa: F401 - kept on the ui facade for tests
    _push_renames,  # noqa: F401 - kept on the ui facade for tests
    _store_registry,
    _sync_export_items,  # noqa: F401 - kept on the ui facade for tests
    _sync_rename_later,
    _viewport_objects,
    rebuild_registry,
)
from .exporters import (
    THEDAS_IO_OT_check_model,
    THEDAS_IO_OT_export_assets,
    THEDAS_IO_OT_export_substance,
    THEDAS_IO_OT_import_substance,
)
from .importers import (
    THEDAS_IO_OT_import_asset,
    THEDAS_IO_OT_import_file,
    _import_row,  # noqa: F401 - kept on the ui facade for tests
)
from .phy.operators import THEDAS_IO_OT_add_collision
from .textures import (
    _cache_dir,
    _relocate_cache,
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
