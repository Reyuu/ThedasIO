# blender ui panels, lists and operators

import sqlite3

import bpy

from .ani.operators import (
    THEDAS_IO_OT_export_animation,
    THEDAS_IO_OT_load_animation,
)
from .asset_browser import (
    THEDAS_IO_OT_assets_page,
    THEDAS_IO_OT_refresh_assets,
    THEDAS_IO_OT_scan_assets,
    _fill_assets,
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
from .datablocks import sweep_orphans as _sweep_orphans  # noqa: F401 - kept on the ui facade
from .export import (
    reset,
)
from .export_registry import (
    _on_depsgraph_update,
    _prune_export_items,  # noqa: F401 - kept on the ui facade for tests
    _push_renames,  # noqa: F401 - kept on the ui facade for tests
    _store_registry,  # noqa: F401 - kept on the ui facade for tests
    _sync_export_items,  # noqa: F401 - kept on the ui facade for tests
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
from .panels import (
    THEDAS_IO_AddonPreferences,
    THEDAS_IO_AssetItem,
    THEDAS_IO_ExportItem,
    THEDAS_IO_PT_assets,
    THEDAS_IO_PT_export,
    THEDAS_IO_PT_import,
    THEDAS_IO_PT_substance,
    THEDAS_IO_RenameItem,
    THEDAS_IO_UL_assets,
    THEDAS_IO_UL_export,
    THEDAS_IO_UL_rename,
    _on_use_toggled,  # noqa: F401 - kept on the ui facade for tests
)
from .phy.operators import THEDAS_IO_OT_add_collision
from .textures import (
    THEDAS_IO_OT_reload_textures,
    _relocate_cache,
    _repack_textures,
    _texture_images,  # noqa: F401 - kept on the ui facade for tests
    load_image,  # noqa: F401 - public helper for tests and scripts
    reload_textures,  # noqa: F401 - public helper for tests and scripts
)

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
