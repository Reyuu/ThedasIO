"""Panels, lists, property groups and add-on preferences for the sidebar."""

from typing import Any, ClassVar

import bpy

from .asset_browser import (
    _asset_limit,
    _invalidate_asset_limit,
    _page_count,
)
from .export import ITEMS, connected, phy_enabled
from .export_registry import _sync_rename_later, _viewport_objects


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


def _on_use_toggled(self: Any, context: Any) -> None:
    # object list checkbox, hides tree but keeps it in blend
    _set_tree_visible(self.filename, self.use)
    _sync_rename_later()


def _path_tail(value: str, keep: int = 2) -> str:
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    return "/".join(parts[-keep:]) if len(parts) > 1 else ""


def _wrap_path(value: str, width: int = 50) -> list:
    # split only on separators, so every character of the path stays visible
    parts = [part for part in value.replace("/", "\\").split("\\") if part]
    lines: list = []
    line = ""
    for part in parts:
        candidate = part if not line else f"{line}\\{part}"
        if line and len(candidate) > width:
            lines.append(line)
            line = part
        else:
            line = candidate
    return lines + [line] if line else lines


def _path_label(layout: Any, label: str, value: str) -> None:
    for i, line in enumerate(_wrap_path(value)):
        prefix = f"{label}: " if i == 0 else " " * (len(label) + 2)
        layout.label(text=prefix + line)


class THEDAS_IO_AssetItem(bpy.types.PropertyGroup):
    package: bpy.props.StringProperty()  # type: ignore
    filename: bpy.props.StringProperty()  # type: ignore
    ext: bpy.props.StringProperty()  # type: ignore
    path: bpy.props.StringProperty()  # type: ignore
    resource: bpy.props.StringProperty()  # type: ignore
    # DA2 entries are named by hash only, so extraction needs it back.
    # 64-bit values do not fit a bpy IntProperty, so keep it as hex text.
    name_hash: bpy.props.StringProperty()  # type: ignore


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


class THEDAS_IO_UL_assets(bpy.types.UIList):
    # filename, extension, resource tail, archive
    COLUMNS = (0.34, 0.10, 0.34, 0.22)

    def draw_item(
        self: Any,
        context: Any,
        layout: Any,
        data: Any,
        item: Any,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None,
        flt_flag: int | None = None,
    ) -> None:
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=f"{item.filename}  ({item.package or '-'})")
            return
        name, ext = item.filename, item.ext or ""
        if ext and name.lower().endswith(ext.lower()):
            name = name[: -len(ext)]

        split = layout.split(factor=self.COLUMNS[0], align=True)
        split.label(text=name)
        split = split.split(factor=self.COLUMNS[1] / (1.0 - self.COLUMNS[0]), align=True)
        split.label(text=ext.lstrip(".") or "-")
        split = split.split(
            factor=self.COLUMNS[2] / (1.0 - self.COLUMNS[0] - self.COLUMNS[1]),
            align=True,
        )
        split.label(text=_path_tail(getattr(item, "resource", "")) or "-")
        split.label(text=item.package or "-")

    @classmethod
    def draw_header(cls: Any, layout: Any) -> None:
        split = layout.split(factor=cls.COLUMNS[0], align=True)
        split.label(text="File")
        split = split.split(factor=cls.COLUMNS[1] / (1.0 - cls.COLUMNS[0]), align=True)
        split.label(text="Type")
        split = split.split(
            factor=cls.COLUMNS[2] / (1.0 - cls.COLUMNS[0] - cls.COLUMNS[1]),
            align=True,
        )
        split.label(text="Resource")
        split.label(text="Archive")


class THEDAS_IO_UL_export(bpy.types.UIList):
    def draw_item(
        self: Any,
        context: Any,
        layout: Any,
        data: Any,
        item: Any,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None,
        flt_flag: int | None = None,
    ) -> None:
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=item.filename)
            return
        split = layout.split(factor=0.12, align=True)
        split.prop(item, "use", text="")
        split.label(text=item.filename)

    @classmethod
    def draw_header(cls: Any, layout: Any) -> None:
        split = layout.split(factor=0.12, align=True)
        split.label(text="Show")
        split.label(text="File")


class THEDAS_IO_UL_rename(bpy.types.UIList):
    def draw_item(
        self: Any,
        context: Any,
        layout: Any,
        data: Any,
        item: Any,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None,
        flt_flag: int | None = None,
    ) -> None:
        if item is None:
            return
        if self.layout_type == "GRID":
            layout.label(text=item.filename)
            return
        split = layout.split(factor=0.5, align=True)
        split.label(text=item.filename)
        split.prop(item, "export_name", text="")

    @classmethod
    def draw_header(cls: Any, layout: Any) -> None:
        split = layout.split(factor=0.5, align=True)
        split.label(text="File")
        split.label(text="Export as")


class THEDAS_IO_PT_assets(bpy.types.Panel):
    bl_label = "ThedasIO Assets"
    bl_idname = "THEDAS_IO_PT_assets"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"

    def draw(self: Any, context: Any) -> None:
        pass


class THEDAS_IO_PT_import(bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "THEDAS_IO_PT_import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ThedasIO"
    bl_parent_id = "THEDAS_IO_PT_assets"
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}  # type: ignore

    def draw(self: Any, context: Any) -> None:
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
            assets = getattr(scene, "thedas_io_assets", None)
            if assets:
                index = max(0, min(int(getattr(scene, "thedas_io_index", 0) or 0), len(assets) - 1))
                selected = assets[index]
                box = self.layout.box()
                box.label(text=f"Selected: {getattr(selected, 'filename', '')}")
                _path_label(box, "Archive", getattr(selected, "path", ""))
                resource = getattr(selected, "resource", "")
                if resource and resource != getattr(selected, "filename", ""):
                    _path_label(box, "Resource", resource)

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

    def draw(self: Any, context: Any) -> None:
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

    def draw(self: Any, context: Any) -> None:
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

    def draw(self: Any, context: Any) -> None:
        if self.layout is None:
            return
        self.layout.prop(self, "thedas_io_game_dir")
        self.layout.prop(self, "thedas_io_da2_game_dir")
        self.layout.prop(self, "thedas_io_enable_phy")
        self.layout.prop(self, "thedas_io_unlimited_assets")
