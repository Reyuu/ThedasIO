from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

import thedas_io as ext
from thedas_io import ui

EXT_DIR = Path(__file__).resolve().parent.parent / "src" / "thedas_io"
# blender's own bpy.ops namespaces, not ours: anything else is addon drift
_BLENDER_OP_PREFIXES = frozenset({"mesh", "object", "wm", "render", "transform"})


def test_manifest():
    manifest = tomllib.loads((EXT_DIR / "blender_manifest.toml").read_bytes().decode())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["id"] == "thedas_io"
    assert manifest["type"] == "add-on"


def test_blender_suite_only_calls_operators_this_extension_registers():
    """The `blender` suite is deselected in CI, so a renamed bl_idname rots in
    silence until someone runs it by hand. The blender suite drives the addon
    through bpy.ops, so compare those calls to the registrations statically --
    cheap enough to live in the fast suite that CI actually runs.

    The builtin allowlist is the one gap: a call into a Blender namespace this
    list misses would pass unchecked. It only guards addon drift, so that is an
    acceptable trade for a test that never spawns Blender."""
    manifest = tomllib.loads((EXT_DIR / "blender_manifest.toml").read_bytes().decode())
    registered = set(
        re.findall(r'bl_idname\s*=\s*"([\w.]+)"', (EXT_DIR / "ui.py").read_text(encoding="utf-8"))
    )

    suite = (Path(__file__).resolve().parent / "test_blender_real.py").read_text(encoding="utf-8")
    called = set(re.findall(r"bpy\.ops\.(\w+)\.(\w+)", suite))
    ours = {f"{prefix}.{op}" for prefix, op in called if prefix == manifest["id"]}
    strays = sorted(
        f"{prefix}.{op}"
        for prefix, op in called
        if prefix not in _BLENDER_OP_PREFIXES and prefix != manifest["id"]
    )

    assert ours, "the blender suite should drive the addon through bpy.ops"
    assert not strays, f"addon operators called under a prefix we do not register: {strays}"
    assert ours <= registered
    assert manifest["blender_version_min"] == "5.2.0"
    assert manifest["name"] and manifest["license"]


def test_every_input_has_a_tooltip():
    """Every drawn input and every button carries name + description.

    layout.prop(text=...) only relabels; the hover tooltip always comes
    from the property registration, and operator buttons from
    bl_description. Walk both so a future input cannot silently ship
    tooltip-less.
    """
    ext.register()
    try:
        scene_props = fake_bpy.bpy.types.Scene
        drawn = (
            "thedas_io_import_source",
            "thedas_io_filter_ext",
            "thedas_io_filter_name",
            "thedas_io_filter_package",
            "thedas_io_scan_progress",
            "thedas_io_disk_file",
            "thedas_io_disk_local",
            "thedas_io_export_dir",
            "thedas_io_export_tint",
            "thedas_io_tint_name",
            "thedas_io_rename_export",
            "thedas_io_sp_dir",
            "thedas_io_sp_split_packed",
            "thedas_io_sp_keep_resolution",
        )
        for name in drawn:
            prop = getattr(scene_props, name)
            assert prop.kwargs.get("description"), name
        operators = [
            ui.THEDAS_IO_OT_scan_assets,
            ui.THEDAS_IO_OT_add_collision,
            ui.THEDAS_IO_OT_load_animation,
            ui.THEDAS_IO_OT_export_animation,
            ui.THEDAS_IO_OT_refresh_assets,
            ui.THEDAS_IO_OT_import_asset,
            ui.THEDAS_IO_OT_import_file,
            ui.THEDAS_IO_OT_export_assets,
            ui.THEDAS_IO_OT_check_model,
            ui.THEDAS_IO_OT_export_substance,
            ui.THEDAS_IO_OT_import_substance,
            ui.THEDAS_IO_OT_reload_textures,
        ]
        assert len(operators) == 12
        for cls in operators:
            assert cls.bl_label, cls.bl_idname
            assert cls.bl_description, cls.bl_idname
            if "directory" in cls.__dict__:
                kwargs = cls.__dict__["directory"].kwargs
                assert kwargs.get("name"), cls.bl_idname
                assert kwargs.get("description"), cls.bl_idname
                assert kwargs.get("subtype") == "DIR_PATH", cls.bl_idname
    finally:
        ext.unregister()


def test_register_unregister():
    ext.register()
    assert fake_bpy.bpy.utils.registered == [
        ui.THEDAS_IO_AssetItem,
        ui.THEDAS_IO_ExportItem,
        ui.THEDAS_IO_RenameItem,
        ui.THEDAS_IO_UL_assets,
        ui.THEDAS_IO_UL_export,
        ui.THEDAS_IO_UL_rename,
        ui.THEDAS_IO_PT_assets,
        ui.THEDAS_IO_PT_import,
        ui.THEDAS_IO_PT_export,
        ui.THEDAS_IO_PT_substance,
        ui.THEDAS_IO_AddonPreferences,
        ui.THEDAS_IO_OT_scan_assets,
        ui.THEDAS_IO_OT_add_collision,
        ui.THEDAS_IO_OT_load_animation,
        ui.THEDAS_IO_OT_export_animation,
        ui.THEDAS_IO_OT_refresh_assets,
        ui.THEDAS_IO_OT_import_asset,
        ui.THEDAS_IO_OT_import_file,
        ui.THEDAS_IO_OT_export_assets,
        ui.THEDAS_IO_OT_check_model,
        ui.THEDAS_IO_OT_reload_textures,
        ui.THEDAS_IO_OT_export_substance,
        ui.THEDAS_IO_OT_import_substance,
    ]
    for prop in (
        "thedas_io_assets",
        "thedas_io_index",
        "thedas_io_export_items",
        "thedas_io_export_index",
        "thedas_io_rename_items",
        "thedas_io_rename_index",
        "thedas_io_scan_progress",
        "thedas_io_scan_active",
        "thedas_io_filter_ext",
        "thedas_io_filter_name",
        "thedas_io_filter_package",
        "thedas_io_db_path",
        "thedas_io_export_dir",
        "thedas_io_import_source",
        "thedas_io_disk_file",
        "thedas_io_disk_local",
        "thedas_io_tint_name",
        "thedas_io_rename_export",
        "thedas_io_sp_split_packed",
        "thedas_io_sp_keep_resolution",
        "thedas_io_sp_dir",
    ):
        assert hasattr(fake_bpy.bpy.types.Scene, prop)
    ext.unregister()
    assert fake_bpy.bpy.utils.registered == []
    for prop in (
        "thedas_io_assets",
        "thedas_io_index",
        "thedas_io_export_items",
        "thedas_io_export_index",
        "thedas_io_rename_items",
        "thedas_io_rename_index",
        "thedas_io_scan_progress",
        "thedas_io_scan_active",
        "thedas_io_filter_ext",
        "thedas_io_filter_name",
        "thedas_io_filter_package",
        "thedas_io_db_path",
        "thedas_io_export_dir",
        "thedas_io_import_source",
        "thedas_io_disk_file",
        "thedas_io_disk_local",
        "thedas_io_tint_name",
        "thedas_io_rename_export",
        "thedas_io_sp_split_packed",
        "thedas_io_sp_keep_resolution",
        "thedas_io_sp_dir",
    ):
        assert not hasattr(fake_bpy.bpy.types.Scene, prop)


def test_addon_preferences():
    from types import SimpleNamespace

    prefs = cast(Any, ui.THEDAS_IO_AddonPreferences())
    assert prefs.bl_idname == ui.__package__
    prefs.draw(SimpleNamespace())
    assert prefs.layout.props == [
        (prefs, "thedas_io_game_dir", ""),
        (prefs, "thedas_io_enable_phy", ""),
    ]
    # experimental collider work stays off until the user turns it on
    ann = cast(Any, ui.THEDAS_IO_AddonPreferences).__annotations__
    phy_prop = ann.get("thedas_io_enable_phy")
    assert phy_prop is not None and phy_prop.kwargs.get("default") is False


def test_substance_subpanel():
    from types import SimpleNamespace

    assert ui.THEDAS_IO_PT_substance.bl_parent_id == "THEDAS_IO_PT_assets"
    assert "DEFAULT_CLOSED" in ui.THEDAS_IO_PT_substance.bl_options
    scene = SimpleNamespace()
    panel = cast(Any, ui.THEDAS_IO_PT_substance())
    panel.draw(SimpleNamespace(scene=scene))
    assert panel.layout.operators == ["thedas_io.export_substance", "thedas_io.import_substance"]
    assert (scene, "thedas_io_sp_dir", "Folder") in panel.layout.props
    assert (scene, "thedas_io_sp_split_packed", "Split packed maps") in panel.layout.props
    assert (
        scene,
        "thedas_io_sp_keep_resolution",
        "Keep Substance resolution",
    ) in panel.layout.props


def test_import_export_subpanels():
    assert ui.THEDAS_IO_PT_import.bl_parent_id == "THEDAS_IO_PT_assets"
    assert ui.THEDAS_IO_PT_export.bl_parent_id == "THEDAS_IO_PT_assets"
    assert "DEFAULT_CLOSED" in ui.THEDAS_IO_PT_import.bl_options
    assert "DEFAULT_CLOSED" in ui.THEDAS_IO_PT_export.bl_options


def test_every_drawn_input_shows_a_real_label():
    """An unlabelled input falls back to Blender's auto label.

    Blender derives the label from the identifier when neither name= nor
    text= is set, so a bare registration shows up as "Thedas Io Weld" in the
    panel. Draw the input panels and require every prop() call to carry a
    label, either name= on registration or text= at the draw site.
    """
    from types import SimpleNamespace

    ext.register()
    try:
        scene = _scene()
        panels = [
            ui.THEDAS_IO_PT_import(),
            ui.THEDAS_IO_PT_export(),
            ui.THEDAS_IO_PT_substance(),
            ui.THEDAS_IO_AddonPreferences(),
        ]
        for panel in panels:
            if isinstance(panel, ui.THEDAS_IO_AddonPreferences):
                panel.draw(SimpleNamespace())
            else:
                panel.draw(SimpleNamespace(scene=scene))

        for panel in panels:
            for _holder, ident, text in panel.layout.props:
                if text:
                    continue
                # the holder is the drawn instance, so its attributes are
                # values; resolve the definition from where it is registered.
                # scene props are setattr'd, prefs props are bare annotations
                prop = getattr(fake_bpy.bpy.types.Scene, ident, None)
                if prop is None:
                    annotations = getattr(ui.THEDAS_IO_AddonPreferences, "__annotations__", {})
                    prop = annotations.get(ident)
                assert prop is not None, ident
                assert prop.kwargs.get("name"), f"{ident} shows no label"
    finally:
        ext.unregister()


def _scene(db="", index=0):
    from types import SimpleNamespace

    from fake_bpy import FakeCollection

    _wm = SimpleNamespace(
        event_timer_add=lambda *a, **kw: SimpleNamespace(delete=lambda: None),
        event_timer_remove=lambda *a, **kw: None,
    )
    return SimpleNamespace(
        thedas_io_assets=FakeCollection(),
        thedas_io_index=index,
        thedas_io_filter_ext="NONE",
        thedas_io_filter_name="",
        thedas_io_filter_package="",
        thedas_io_db_path=db,
        thedas_io_export_dir="",
        thedas_io_scan_active=False,
        thedas_io_scan_progress=0,
        thedas_io_scan_addins=False,
        thedas_io_addins_dir="",
        thedas_io_import_source="GAME",
        thedas_io_export_tint=False,
        thedas_io_rename_export=False,
        thedas_io_weld=True,
        window_manager=_wm,
        window=SimpleNamespace(),
    )


def _seed_db(tmp_path):
    from thedas_io.asset_db.scanner import scan
    from thedas_io.erf.description import ERFWriter

    w = ERFWriter()
    w.add_entry("a.msh", b"mesh-bytes")
    w.add_entry("b.txt", b"text")
    (tmp_path / "p.erf").write_bytes(w.write())
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    return db


def test_browser_refresh_and_draw(tmp_path):
    from types import SimpleNamespace

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    assert cast(Any, ui.THEDAS_IO_OT_refresh_assets()).execute(
        SimpleNamespace(scene=scene, window_manager=scene.window_manager, window=None)
    ) == {"FINISHED"}
    assert [(i.filename, i.ext) for i in scene.thedas_io_assets] == [
        ("a.msh", ".msh"),
        ("b.txt", ".txt"),
    ]

    scene.thedas_io_filter_ext = "MSH"
    assert cast(Any, ui.THEDAS_IO_OT_refresh_assets()).execute(
        SimpleNamespace(scene=scene, window_manager=scene.window_manager, window=None)
    ) == {"FINISHED"}
    assert [i.filename for i in scene.thedas_io_assets] == ["a.msh"]

    scene.thedas_io_filter_ext = "NONE"
    assert cast(Any, ui.THEDAS_IO_OT_refresh_assets()).execute(
        SimpleNamespace(scene=scene, window_manager=scene.window_manager, window=None)
    ) == {"FINISHED"}
    assert [i.filename for i in scene.thedas_io_assets] == ["a.msh", "b.txt"]

    # the collision row is gated behind the experimental PHY switch
    panel = cast(Any, ui.THEDAS_IO_PT_import())
    panel.draw(SimpleNamespace(scene=scene))
    assert "thedas_io.add_collision" not in panel.layout.operators
    assert "thedas_io.load_animation" not in panel.layout.operators

    phy_ctx = SimpleNamespace(
        scene=scene,
        preferences=SimpleNamespace(addons={
            "thedas_io": SimpleNamespace(preferences=SimpleNamespace(
                thedas_io_enable_phy=True))}),
    )
    panel = cast(Any, ui.THEDAS_IO_PT_import())
    panel.draw(phy_ctx)
    assert panel.layout.operators == [
        "thedas_io.refresh_assets",
        "thedas_io.scan_assets",
        "thedas_io.add_collision",
        "thedas_io.import_asset",
    ]
    assert panel.layout.lists == [("THEDAS_IO_UL_assets", "thedas_io_assets")]

    export_panel = cast(Any, ui.THEDAS_IO_PT_export())
    export_panel.draw(SimpleNamespace(scene=scene))
    assert export_panel.layout.operators == [
        "thedas_io.reload_textures",
        "thedas_io.check_model",
        "thedas_io.export_animation",
        "thedas_io.export_assets",
    ]
    assert export_panel.layout.lists == [("THEDAS_IO_UL_export", "thedas_io_export_items")]
    assert "Export as" not in export_panel.layout.labels  # rename unticked: no table

    scene.thedas_io_rename_export = True
    renamed_panel = cast(Any, ui.THEDAS_IO_PT_export())
    renamed_panel.draw(SimpleNamespace(scene=scene))
    assert "Export as" in renamed_panel.layout.labels
    assert "File" in renamed_panel.layout.labels and "Show" in renamed_panel.layout.labels
    assert renamed_panel.layout.lists == [
        ("THEDAS_IO_UL_export", "thedas_io_export_items"),
        ("THEDAS_IO_UL_rename", "thedas_io_rename_items"),
    ]

    scene.thedas_io_import_source = "DISK"
    disk_panel = cast(Any, ui.THEDAS_IO_PT_import())
    disk_panel.draw(SimpleNamespace(scene=scene))
    assert disk_panel.layout.operators == ["thedas_io.import_file"]
    assert disk_panel.layout.lists == []
    disk_export = cast(Any, ui.THEDAS_IO_PT_export())
    disk_export.draw(SimpleNamespace(scene=scene))
    assert disk_export.layout.operators == [
        "thedas_io.reload_textures",
        "thedas_io.check_model",
        "thedas_io.export_animation",
        "thedas_io.export_assets",
    ]
    assert disk_export.layout.lists == [
        ("THEDAS_IO_UL_export", "thedas_io_export_items"),
        ("THEDAS_IO_UL_rename", "thedas_io_rename_items"),
    ]


def test_rename_table_lists_checked_trees_files():
    # Rename rows are the exported files of checked roots; objects untouched.
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        mat = SimpleNamespace(name="MAT")
        obj2 = SimpleNamespace(type="MESH", name="o2", data=SimpleNamespace(materials=[mat]))
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={6005: "a.msh"}),
            "meshes": [obj2],
            "hosts": {},
        }
        ITEMS["a.msh"] = {"file": "a.msh", "kind": "msh", "objects": [(obj2, SimpleNamespace())]}
        ITEMS["b.mao"] = {
            "file": "b.mao",
            "kind": "mao",
            "model": {"name": "MAT", "textures": {"t": "c.dds"}},
        }
        ITEMS["c.dds"] = {"file": "c.dds", "kind": "texture", "objects": []}
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(), thedas_io_rename_items=FakeCollection()
        )
        ui._sync_export_items(scene)
        assert [r.filename for r in scene.thedas_io_export_items] == ["d.mmh"]
        assert sorted(r.filename for r in scene.thedas_io_rename_items) == [
            "a.msh",
            "b.mao",
            "c.dds",
            "d.mmh",
        ]
        scene.thedas_io_rename_items[0].export_name = "x_l2.mmh"
        ui._sync_rename_items(scene)  # edits survive a re-sync
        assert scene.thedas_io_rename_items[0].export_name == "x_l2.mmh"
        scene.thedas_io_export_items[0].use = False
        ui._sync_rename_items(scene)  # unchecked tree drops its files
        assert list(scene.thedas_io_rename_items) == []
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_tint_row_appears_and_renames_the_tnt():
    # With tint on, the rename table lists each checked mmh's .tnt file.
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={}),
            "meshes": [SimpleNamespace(name="o")],
            "hosts": {},
        }
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(),
            thedas_io_rename_items=FakeCollection(),
            thedas_io_export_tint=True,
            thedas_io_tint_name="",
            thedas_io_rename_export=True,
        )
        ui._sync_export_items(scene)
        assert [r.filename for r in scene.thedas_io_rename_items] == ["d.mmh", "d.tnt"]
        for r in scene.thedas_io_rename_items:
            if r.filename == "d.tnt":
                r.export_name = "t3_arm_rlr"
        ui._push_renames(scene)
        assert ITEMS["d.mmh"].get("as_tnt") == "t3_arm_rlr.tnt"
        scene.thedas_io_rename_export = False
        ui._push_renames(scene)  # unticked clears it
        assert "as_tnt" not in ITEMS["d.mmh"]
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_rename_sync_ignores_a_depsgraph_re_entry():
    # _push_renames syncs from inside the export operator and _prune_export_items
    # syncs from the depsgraph handler. An export that touches images can fire
    # that handler in between, and rebuilding the collection twice over is what
    # takes Blender down.
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    saved = dict(ITEMS)
    ITEMS.clear()
    fired = []
    try:
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={}),
            "meshes": [SimpleNamespace(name="o")],
            "hosts": {},
        }
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(),
            thedas_io_rename_items=FakeCollection(),
            thedas_io_export_tint=False,
            thedas_io_tint_name="",
            thedas_io_rename_export=True,
        )
        ui._sync_export_items(scene)

        real = ui.connected

        def reentering(checked):
            fired.append(checked)
            ui._sync_rename_items(scene)  # the depsgraph handler, mid-sync
            return real(checked)

        ui.connected = reentering
        try:
            ui._sync_rename_items(scene)
        finally:
            ui.connected = real

        assert len(fired) == 1, "the inner sync must not re-enter connected()"
        assert [r.filename for r in scene.thedas_io_rename_items] == ["d.mmh"]
        assert not ui._SYNCING_RENAMES, "the guard must be released again"
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_push_renames_survives_its_rows_being_rebuilt():
    # The depsgraph handler can rebuild the rename list while _push_renames is
    # still walking it; a snapshot keeps the walk honest.
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        ITEMS["a.msh"] = {"file": "a.msh", "kind": "msh", "objects": []}
        ITEMS["b.msh"] = {"file": "b.msh", "kind": "msh", "objects": []}

        class Tripping:
            # stands in for the depsgraph handler: reading the name empties the
            # list that _push_renames is still walking
            def __init__(self, rows, visits):
                self.filename = ""
                self._rows = rows
                self._visits = visits

            @property
            def export_name(self):
                self._visits.append(self.filename)
                while len(self._rows):
                    self._rows.remove(len(self._rows) - 1)
                return ""

        class Rebuilding(FakeCollection):
            def __init__(self):
                super().__init__()
                self.visits: list = []

            def add(self):
                row = Tripping(self, self.visits)
                self.append(row)
                return row

        renames = Rebuilding()
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(),
            thedas_io_rename_items=renames,
            thedas_io_export_tint=False,
            thedas_io_tint_name="",
            thedas_io_rename_export=True,
        )
        for name in ("a.msh", "b.msh"):
            checked = scene.thedas_io_export_items.add()
            checked.filename = name
            checked.use = True

        ui._push_renames(scene)

        # both rows were walked even though the list emptied itself on the first
        assert sorted(renames.visits) == ["a.msh", "b.msh"]
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_deleting_the_hierarchy_drops_its_row():
    # Deleting the parent (armature) or the whole tree removes the object row.
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        chunk = SimpleNamespace(name="chunk")
        arm = SimpleNamespace(name="arm")
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={6005: "a.msh"}),
            "meshes": [chunk],
            "armature": arm,
            "hosts": {},
        }
        ITEMS["a.msh"] = {"file": "a.msh", "kind": "msh", "objects": [(chunk, SimpleNamespace())]}
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(), thedas_io_rename_items=FakeCollection()
        )
        ui._sync_export_items(scene)
        assert [r.filename for r in scene.thedas_io_export_items] == ["d.mmh"]
        assert sorted(r.filename for r in scene.thedas_io_rename_items) == ["a.msh", "d.mmh"]

        class Gone:
            # What Blender leaves behind after an object is deleted.

            @property
            def name(self):
                raise ReferenceError("deleted")

        ITEMS["d.mmh"]["armature"] = Gone()  # parent gone
        assert ui._prune_export_items(scene) is True
        assert list(scene.thedas_io_export_items) == []
        assert list(scene.thedas_io_rename_items) == []
        assert ui._prune_export_items(scene) is False  # nothing left to do

        # whole tree gone: a later sync must not resurrect the row either
        ITEMS["a.msh"]["objects"] = []
        ITEMS["d.mmh"]["meshes"] = [Gone()]
        ITEMS["d.mmh"]["armature"] = None
        ui._sync_export_items(scene)
        assert list(scene.thedas_io_export_items) == []
        assert list(scene.thedas_io_rename_items) == []
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_unticking_hides_the_whole_tree_but_keeps_it():
    # Uncheck hides every object under the root; blend data stays for re-tick.
    from types import SimpleNamespace

    from thedas_io.export import ITEMS

    chunk = SimpleNamespace(name="chunk", hide_viewport=False)
    host = SimpleNamespace(name="host", hide_viewport=False)
    arm = SimpleNamespace(name="arm", hide_viewport=False)
    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={6005: "a.msh"}),
            "meshes": [],
            "armature": arm,
            "hosts": {"p": ("object", host, None), "q": ("bone", (arm, "GOB"), None)},
        }
        ITEMS["a.msh"] = {"file": "a.msh", "kind": "msh", "objects": [(chunk, SimpleNamespace())]}
        from fake_bpy import FakeCollection

        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(), thedas_io_rename_items=FakeCollection()
        )
        ctx = SimpleNamespace(scene=scene)
        row = SimpleNamespace(filename="d.mmh", use=False)
        ui._on_use_toggled(row, ctx)
        assert chunk.hide_viewport is True
        assert host.hide_viewport is True
        assert arm.hide_viewport is True
        row.use = True
        ui._on_use_toggled(row, ctx)
        assert chunk.hide_viewport is False
        assert host.hide_viewport is False
        assert arm.hide_viewport is False
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_export_list_sync_never_mutates_during_draw():
    """Rows sync on import, not in draw(): mutating the displayed collection
    mid-draw aborts the rest of the panel (Export button included)."""
    import inspect
    from types import SimpleNamespace

    from fake_bpy import FakeCollection
    from thedas_io.export import ITEMS

    draw_src = inspect.getsource(ui.THEDAS_IO_PT_export.draw)
    assert "_sync_export_items" not in draw_src
    saved = dict(ITEMS)
    ITEMS.clear()
    try:
        obj = SimpleNamespace(name="o")
        ITEMS["d.mmh"] = {
            "file": "d.mmh",
            "kind": "mmh",
            "model": SimpleNamespace(values={6005: "a.msh"}),
            "meshes": [obj],
            "hosts": {},
        }
        ITEMS["a.msh"] = {"file": "a.msh", "kind": "msh", "objects": [(obj, SimpleNamespace())]}
        ITEMS["b.mao"] = {"file": "b.mao", "kind": "mao", "model": {}}
        scene = SimpleNamespace(
            thedas_io_export_items=FakeCollection(), thedas_io_rename_items=FakeCollection()
        )
        ui._sync_export_items(scene)
        # one hierarchy, one row: the referenced msh folds into its mmh
        assert [r.filename for r in scene.thedas_io_export_items] == ["d.mmh"]
    finally:
        ITEMS.clear()
        ITEMS.update(saved)


def test_extension_combo_items_and_filter(tmp_path):
    import fake_bpy

    assert [item[0] for item in ui._EXT_ITEMS] == ["NONE", "MSH", "MMH",
                                                   "ANI", "CUB"]
    assert [item[1] for item in ui._EXT_ITEMS] == ["(None)", "MSH", "MMH",
                                                   "ANI", "CUB"]
    assert ui._ext_filter("NONE") is None
    assert ui._ext_filter("") is None
    assert ui._ext_filter("MSH") == "MSH"
    assert ui._ext_filter("MMH") == "MMH"
    # a .blend saved with the old free-text field must not break opening
    scene = _scene(str(_seed_db(tmp_path)))
    scene.thedas_io_filter_ext = ".msh"
    fake_bpy.bpy.data.scenes.append(scene)
    try:
        ui._on_file_load(None)
        assert scene.thedas_io_filter_ext == "NONE"
        scene.thedas_io_filter_ext = "MMH"
        ui._on_file_load(None)
        assert scene.thedas_io_filter_ext == "MMH"
    finally:
        fake_bpy.bpy.data.scenes.remove(scene)


def test_autoload_fills_empty_list(tmp_path):
    import fake_bpy

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    assert len(scene.thedas_io_assets) == 0
    fake_bpy.bpy.data.scenes.append(scene)
    try:
        ui._on_file_load(None)
    finally:
        fake_bpy.bpy.data.scenes.remove(scene)
    assert [i.filename for i in scene.thedas_io_assets] == ["a.msh", "b.txt"]


def test_autoload_leaves_nonempty_list(tmp_path):
    import fake_bpy
    from fake_bpy import FakeCollection

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    scene.thedas_io_assets = FakeCollection()
    scene.thedas_io_assets.add().filename = "keep.msh"
    fake_bpy.bpy.data.scenes.append(scene)
    try:
        ui._on_file_load(None)
    finally:
        fake_bpy.bpy.data.scenes.remove(scene)
    assert [i.filename for i in scene.thedas_io_assets] == ["keep.msh"]


def test_draw_item_columns():
    from types import SimpleNamespace

    from fake_bpy import FakeLayout

    ul = cast(Any, ui.THEDAS_IO_UL_assets())
    ul.layout_type = "DEFAULT"
    layout = FakeLayout()
    item = SimpleNamespace(filename="hf_arm_hvya_0.mmh", ext=".mmh", package="modelhierarchies.erf")
    ul.draw_item(SimpleNamespace(), layout, None, item, 0, None, "", 0)
    assert layout.labels == ["hf_arm_hvya_0", "mmh", "modelhierarchies.erf"]
    bare = FakeLayout()
    ul.draw_item(
        SimpleNamespace(),
        bare,
        None,
        SimpleNamespace(filename="x", ext="", package=""),
        0,
        None,
        "",
        0,
    )
    assert bare.labels == ["x", "—", "—"]


def test_scan_assets(tmp_path):
    from types import SimpleNamespace

    from thedas_io.asset_db.scanner import find

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    ctx = SimpleNamespace(
        scene=scene, window_manager=scene.window_manager, window=SimpleNamespace()
    )
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op.directory = str(tmp_path)
    assert op.execute(ctx) == {"FINISHED"}
    assert [i.filename for i in scene.thedas_io_assets] == [
        i["filename"] for i in find(db_path=str(db))
    ]
    assert op.reports[-1][0] == {"INFO"}

    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    assert op.execute(
        SimpleNamespace(
            scene=_scene(), window_manager=scene.window_manager, window=SimpleNamespace()
        )
    ) == {"CANCELLED"}  # no folder anywhere


def test_scan_refuses_second_run(tmp_path):
    # A second Scan while one runs cancels instead of racing it on sqlite.
    from types import SimpleNamespace

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    scene.thedas_io_scan_active = True
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op.directory = str(tmp_path)
    ctx = SimpleNamespace(
        scene=scene, window_manager=scene.window_manager, window=SimpleNamespace()
    )
    assert op.execute(ctx) == {"CANCELLED"}
    assert op.invoke(ctx, SimpleNamespace(type="NONE")) == {"CANCELLED"}
    assert any("already running" in text for _, text in op.reports)


def test_file_load_clears_stuck_scan_flag():
    """A .blend saved (or crashed) mid-scan reopens with no thread behind
    the active flag; without a reset Scan would refuse forever."""
    scene = _scene()
    scene.thedas_io_scan_active = True
    scene.thedas_io_scan_progress = 63
    fake_bpy.bpy.data.scenes.append(scene)
    try:
        ui._on_file_load(None)
    finally:
        fake_bpy.bpy.data.scenes.remove(scene)
    assert scene.thedas_io_scan_active is False
    assert scene.thedas_io_scan_progress == 0


def test_scan_thread_progress_and_tick(tmp_path):
    import time
    from types import SimpleNamespace

    from thedas_io.ui import _scan_async

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    ctx = SimpleNamespace(scene=scene)
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op._scene = scene
    op._db_path = str(db)
    op._state = _scan_async(str(tmp_path), str(db))
    status = {"RUNNING_MODAL"}
    for _ in range(500):
        status = op._tick(ctx)
        if status != {"RUNNING_MODAL"}:
            break
        time.sleep(0.01)
    assert status == {"FINISHED"}
    assert scene.thedas_io_scan_progress == 100
    assert scene.thedas_io_scan_active is False
    assert [i.filename for i in scene.thedas_io_assets] == ["a.msh", "b.txt"]
    assert op._state["current"] == op._state["total"] > 0


def test_scan_assets_prefers_game_dir(tmp_path):
    from types import SimpleNamespace

    scene = _scene(str(tmp_path / "t.sqlite"))
    addons = {
        "thedas_io": SimpleNamespace(preferences=SimpleNamespace(thedas_io_game_dir=str(tmp_path)))
    }
    ctx = SimpleNamespace(
        scene=scene,
        preferences=SimpleNamespace(addons=addons),
        window_manager=scene.window_manager,
        window=SimpleNamespace(),
    )
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    _seed_db(tmp_path)
    assert op.execute(ctx) == {"FINISHED"}
    assert [i.filename for i in scene.thedas_io_assets]


def test_scan_assets_includes_addins_only_when_asked(tmp_path):
    from types import SimpleNamespace

    from thedas_io.asset_db.scanner import find_exact

    game = tmp_path / "game"
    addins = tmp_path / "AddIns"
    game.mkdir()
    addins.mkdir()
    (game / "g.msh").write_bytes(b"g")
    (addins / "d.msh").write_bytes(b"d")

    scene = _scene(str(tmp_path / "t.sqlite"))
    scene.thedas_io_addins_dir = str(addins)
    ctx = SimpleNamespace(
        scene=scene,
        preferences=SimpleNamespace(addons={}),
        window_manager=scene.window_manager,
        window=SimpleNamespace(),
    )

    scene.thedas_io_scan_addins = False
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op.directory = str(game)
    assert op.execute(ctx) == {"FINISHED"}
    # off: the AddIns folder is not indexed at all
    assert {i.filename for i in scene.thedas_io_assets} == {"g.msh"}

    # on: both folders, and the DLC copy is the one that resolves
    scene.thedas_io_scan_addins = True
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op.directory = str(game)
    assert op.execute(ctx) == {"FINISHED"}
    assert {i.filename for i in scene.thedas_io_assets} == {"g.msh", "d.msh"}
    hit = find_exact("d.msh", db_path=str(tmp_path / "t.sqlite"))
    assert hit is not None and hit["path"] == str(addins / "d.msh")


def test_scan_assets_reports_a_missing_addins_folder(tmp_path):
    from types import SimpleNamespace

    game = tmp_path / "game"
    game.mkdir()
    scene = _scene(str(tmp_path / "t.sqlite"))
    scene.thedas_io_scan_addins = True
    scene.thedas_io_addins_dir = str(tmp_path / "nope")
    reports: list = []
    ctx = SimpleNamespace(
        scene=scene,
        preferences=SimpleNamespace(addons={}),
        window_manager=scene.window_manager,
        window=SimpleNamespace(),
    )
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op.report = lambda level, msg: reports.append(msg)
    op.directory = str(game)
    assert op.execute(ctx) == {"CANCELLED"}
    assert any("AddIns folder not found" in m for m in reports)


def test_mao_for_node_prefers_6001(tmp_path):
    from types import SimpleNamespace

    from thedas_io.asset_db.scanner import scan
    from thedas_io.erf.description import ERFWriter
    from thedas_io.ui import _mao_for_node

    (tmp_path / "x.mao").write_bytes(b"<MaterialObject/>")
    w = ERFWriter()
    w.add_entry("pf_arm_hvya.mao", b"<MaterialObject/>")
    (tmp_path / "p.erf").write_bytes(w.write())
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    row = {"package": "p.erf", "filename": "x.mmh", "ext": ".mmh", "path": str(tmp_path / "p.erf")}
    node = SimpleNamespace(values={6001: "PF_ARM_HVYa"})
    hit, tried = _mao_for_node(node, "x", row, str(db))
    assert hit is not None and hit.read_bytes() == b"<MaterialObject/>"
    assert tried == ["PF_ARM_HVYa.mao", "x.mao"]
    hit2, _ = _mao_for_node(SimpleNamespace(values={}), "x", row, str(db))
    assert hit2 is not None and hit2.name == "x.mao"  # stem fallback
    hit3, tried3 = _mao_for_node(SimpleNamespace(values={}), "nope", row, str(db))
    assert hit3 is None and tried3 == ["nope.mao"]


def test_import_dispatch(tmp_path):
    from types import SimpleNamespace

    db = _seed_db(tmp_path)
    scene = _scene(str(db))
    ctx = SimpleNamespace(
        scene=scene, window_manager=scene.window_manager, window=SimpleNamespace()
    )
    cast(Any, ui.THEDAS_IO_OT_refresh_assets()).execute(ctx)
    # Now only 2 entries: a.msh (index 0) and b.txt (index 1)
    scene.thedas_io_index = 1
    op = cast(Any, ui.THEDAS_IO_OT_import_asset())
    assert op.execute(ctx) == {"CANCELLED"}  # .txt has no importer
    assert op.reports == [({"WARNING"}, "No importer for b.txt")]

    scene.thedas_io_index = 99
    op = cast(Any, ui.THEDAS_IO_OT_import_asset())
    assert op.execute(ctx) == {"CANCELLED"}  # nothing selected
    assert op.reports == [({"WARNING"}, "No asset selected")]


def test_ui_needs_bpy():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import thedas_io.ui"],
        capture_output=True,
        cwd=str(EXT_DIR.parent.parent),
        check=False,
    )
    assert proc.returncode != 0
    assert b"bpy" in proc.stderr


def test_stock_tint_survives_a_material_round_trip():
    # A .tnt -> DAO Tint group -> .tnt trip must lose no float at all.
    from types import SimpleNamespace

    from thedas_io import tnt
    from thedas_io.export import _group_tint

    raw = (EXT_DIR.parent.parent / "data" / "t1_mub_bk1.tnt").read_bytes()
    values = tnt.parse(raw)
    merged = {}
    for side in tnt.SIDES:
        inputs = {
            name: SimpleNamespace(default_value=(0.0, 0.0, 0.0, 1.0)) for name in tnt.for_side(side)
        }
        inputs[f"{side} opacity A"] = SimpleNamespace(default_value=0.0)
        group = SimpleNamespace(
            type="GROUP", inputs=inputs, node_tree=SimpleNamespace(name=tnt.group_name(side))
        )
        for name, colour in values.items():
            ui._seed_tint(group, name, colour)
        merged.update(_group_tint(group))
    assert tnt.build(merged) == raw


def _node(name, kind, loc, children=()):
    from types import SimpleNamespace

    return SimpleNamespace(
        name=name, kind=kind, values={}, children=list(children), loc=loc
    )


def test_bone_length_is_the_distance_to_the_first_child_bone():
    from thedas_io.ui import _bone_lengths

    # a chain: spine -> chest -> neck. each bone spans to the next bone's head.
    neck = _node("neck", "node", (0.0, 0.0, 1.40))
    chest = _node("chest", "node", (0.0, 0.0, 1.20), [neck])
    spine = _node("spine", "node", (0.0, 0.0, 0.0), [chest])
    rows = [(spine, None, (0.0, 0.0, 0.0), None), (chest, spine, (0.0, 0.0, 1.2), None),
            (neck, chest, (0.0, 0.0, 1.4), None)]

    out = _bone_lengths(rows)
    assert abs(out[id(spine)] - 1.20) < 1e-9
    assert abs(out[id(chest)] - 0.20) < 1e-9
    assert id(neck) not in out  # a leaf has nothing to measure to


def test_bone_length_skips_non_bone_children():
    from thedas_io.ui import _bone_lengths

    # a hook empty is not a bone, so the chain continues past it
    hand = _node("hand", "node", (0.0, 0.0, 1.0))
    hook = _node("hook", "crst", (0.0, 0.0, 0.5))
    arm = _node("arm", "node", (0.0, 0.0, 0.0), [hook, hand])
    rows = [(arm, None, (0.0, 0.0, 0.0), None), (hand, arm, (0.0, 0.0, 1.0), None)]

    assert abs(_bone_lengths(rows)[id(arm)] - 1.0) < 1e-9


def test_a_child_sitting_on_its_parent_does_not_make_a_zero_length_bone():
    from thedas_io.ui import _LEAF_BONE_LEN, _bone_lengths

    # helper bones often share their parent's head; that measures 0, and a
    # zero-length bone is degenerate in Blender
    stub = _node("stub", "node", (0.0, 0.0, 1.0))
    arm = _node("arm", "node", (0.0, 0.0, 1.0), [stub])
    rows = [(arm, None, (0.0, 0.0, 1.0), None), (stub, arm, (0.0, 0.0, 1.0), None)]

    assert id(arm) not in _bone_lengths(rows)  # falls back to the stub
    assert _LEAF_BONE_LEN > 0.0


def test_rig_does_not_depend_on_the_mesh_it_is_bound_to():
    # DAO reuses one rig across LODs and outfit variations. the same mmh must
    # produce the same bones no matter which msh is loaded with it, or LODs
    # disagree and every .ani/.cub that targets the rig stops lining up.
    from thedas_io.ui import _bone_lengths

    neck = _node("neck", "node", (0.0, 0.0, 1.40))
    chest = _node("chest", "node", (0.0, 0.0, 1.20), [neck])
    spine = _node("spine", "node", (0.0, 0.0, 0.0), [chest])
    rows = [(spine, None, (0.0, 0.0, 0.0), None), (chest, spine, (0.0, 0.0, 1.2), None),
            (neck, chest, (0.0, 0.0, 1.4), None)]

    import inspect

    from thedas_io import ui

    src = inspect.getsource(ui._bone_lengths)
    for leak in ("chunks", "vertices", "bbox", "scale", "cloud"):
        assert leak not in src, f"bone length must not read {leak} from the mesh"
    assert _bone_lengths(rows) == _bone_lengths(rows)


def test_scan_report_names_unreadable_archives(tmp_path):
    # a corrupt .rim still counts in `files`, so without this the summary reads
    # like a folder that simply held nothing
    from types import SimpleNamespace

    from thedas_io.asset_db.scanner import scan

    db = _seed_db(tmp_path)
    (tmp_path / "corrupt.rim").write_bytes(b"not an erf")
    stats = scan(tmp_path, db)
    assert stats["skipped"] == [str(tmp_path / "corrupt.rim")]

    scene = _scene(db=str(db))
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op._scene = scene
    op._db_path = str(db)
    op._state = {"stats": stats, "error": None}
    op._finish(SimpleNamespace(scene=scene))

    assert len(op.reports) == 1
    assert "1 unreadable archive(s)" in op.reports[0][1]


def test_a_scan_worker_that_dies_is_not_reported_as_an_empty_folder(monkeypatch, tmp_path):
    # the worker whitelisted four exception types, so anything else killed the
    # thread silently and the summary claimed a successful scan of nothing
    from thedas_io import ui as ui_module

    def explode(*args, **kwargs):
        raise MemoryError("out of memory")

    monkeypatch.setattr(ui_module, "scan", explode)
    state = ui_module._scan_async(tmp_path, tmp_path / "t.sqlite")
    state["thread"].join(timeout=10)

    assert state["done"] is True
    assert isinstance(state["error"], MemoryError)


def test_finish_treats_missing_stats_as_a_failed_scan(tmp_path):
    from types import SimpleNamespace

    scene = _scene(db=str(tmp_path / "t.sqlite"))
    op = cast(Any, ui.THEDAS_IO_OT_scan_assets())
    op._scene = scene
    op._db_path = str(tmp_path / "t.sqlite")
    op._state = {"stats": None, "error": None}

    assert op._finish(SimpleNamespace(scene=scene)) == {"CANCELLED"}
    assert op.reports[0][0] == {"WARNING"}
    assert "failed" in op.reports[0][1].lower()
