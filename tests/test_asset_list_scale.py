from __future__ import annotations

from types import SimpleNamespace

import fake_bpy
from conftest import archive
from fake_bpy import FakeCollection
from thedas_io import ui


def _make_db(tmp_path, n=2500):
    archive(tmp_path / "big.erf", {f"f{i:05d}.msh": b"x" for i in range(n)})
    archive(tmp_path / "other.erf", {"needle.msh": b"y"})
    from thedas_io.asset_db.scanner import scan

    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    return db


def test_find_limit_and_count(tmp_path):
    from thedas_io.asset_db.scanner import count, find

    db = _make_db(tmp_path, 2500)
    assert count(db_path=db) == 2501
    assert count(name="needle", db_path=db) == 1
    rows = find(db_path=db, limit=1000)
    assert len(rows) == 1000
    assert len(find(name="needle", db_path=db, limit=1000)) == 1
    assert len(find(db_path=db, limit=None)) == 2501
    assert len(find(db_path=db, limit=10, offset=2500)) == 1


def test_fill_assets_caps_by_default(tmp_path):
    db = _make_db(tmp_path, 2500)
    scene = SimpleNamespace(thedas_io_assets=FakeCollection())
    fake_bpy.bpy.data.scenes.append(scene)
    try:
        rows, total = ui._fill_assets(scene, str(db))
    finally:
        fake_bpy.bpy.data.scenes.remove(scene)
    assert total == 2501
    assert len(rows) == ui.ASSET_LIMIT
    assert len(list(scene.thedas_io_assets)) == ui.ASSET_LIMIT


def test_fill_assets_unlimited_explicit(tmp_path):
    db = _make_db(tmp_path, 500)
    scene = SimpleNamespace(thedas_io_assets=FakeCollection())
    rows, total = ui._fill_assets(scene, str(db), limit=None)
    assert total == 501
    assert len(rows) == 501


def test_prefs_unlimited_toggles_limit(tmp_path):
    db = _make_db(tmp_path, 100)
    scene = SimpleNamespace(thedas_io_assets=FakeCollection())
    prefs_on = SimpleNamespace(
        preferences=SimpleNamespace(
            addons={
                "thedas_io": SimpleNamespace(
                    preferences=SimpleNamespace(thedas_io_unlimited_assets=True)
                )
            }
        )
    )
    prefs_off = SimpleNamespace(
        preferences=SimpleNamespace(
            addons={
                "thedas_io": SimpleNamespace(
                    preferences=SimpleNamespace(thedas_io_unlimited_assets=False)
                )
            }
        )
    )
    assert ui._prefs_unlimited(prefs_on) is True
    assert ui._prefs_unlimited(prefs_off) is False
    assert ui._asset_limit(prefs_on) is None
    assert ui._asset_limit(prefs_off) == ui.ASSET_LIMIT
    rows, total = ui._fill_assets(scene, str(db), context=prefs_on)
    assert len(rows) == total == 101


def _page_scene(db, **over):
    from fake_bpy import FakeCollection

    base = {
        "thedas_io_assets": FakeCollection(),
        "thedas_io_index": 0,
        "thedas_io_page": 0,
        "thedas_io_assets_total": 0,
        "thedas_io_filter_ext": "NONE",
        "thedas_io_filter_name": "",
        "thedas_io_filter_package": "",
        "thedas_io_db_path": str(db),
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(scene):
    return SimpleNamespace(scene=scene)


def test_pages_partition_without_overlap(tmp_path):
    from thedas_io.asset_db.scanner import find

    db = _make_db(tmp_path, 2500)
    p0 = find(db_path=db, limit=1000, offset=0)
    p1 = find(db_path=db, limit=1000, offset=1000)
    p2 = find(db_path=db, limit=1000, offset=2000)
    assert (len(p0), len(p1), len(p2)) == (1000, 1000, 501)
    names = [r["filename"] for r in (*p0, *p1, *p2)]
    assert len(set(names)) == 2501
    # stable order: repeat reads match
    again = [r["filename"] for r in find(db_path=db, limit=1000, offset=1000)]
    assert [r["filename"] for r in p1] == again


def test_refresh_resets_and_page_operator_walks(tmp_path):
    from typing import cast

    db = _make_db(tmp_path, 2500)
    scene = _page_scene(db)
    ctx = _ctx(scene)

    assert cast(object, ui.THEDAS_IO_OT_refresh_assets()).execute(ctx) == {"FINISHED"}
    assert (scene.thedas_io_page, scene.thedas_io_assets_total) == (0, 2501)
    first = [i.filename for i in scene.thedas_io_assets]
    assert len(first) == 1000

    def turn(step):
        op = ui.THEDAS_IO_OT_assets_page()
        op.step = step
        assert cast(object, op).execute(ctx) == {"FINISHED"}

    turn(1)
    assert scene.thedas_io_page == 1
    second = [i.filename for i in scene.thedas_io_assets]
    assert len(second) == 1000
    assert not set(first) & set(second)

    turn(1)
    assert scene.thedas_io_page == 2
    assert len(list(scene.thedas_io_assets)) == 501

    turn(1)  # clamped at last page
    assert scene.thedas_io_page == 2
    assert len(list(scene.thedas_io_assets)) == 501

    turn(-1)
    assert scene.thedas_io_page == 1
    assert [i.filename for i in scene.thedas_io_assets] == second

    assert cast(object, ui.THEDAS_IO_OT_refresh_assets()).execute(ctx) == {"FINISHED"}
    assert scene.thedas_io_page == 0
    assert [i.filename for i in scene.thedas_io_assets] == first


def test_panel_pagination_row_only_when_needed(tmp_path):
    from typing import cast

    from fake_bpy import FakeCollection, FakeLayout
    from thedas_io import ui as _ui

    db = _make_db(tmp_path, 10)

    def draw_with(total, n_items, page=0):
        scene = SimpleNamespace(
            thedas_io_import_source="GAME",
            thedas_io_weld=True,
            thedas_io_filter_ext="NONE",
            thedas_io_filter_name="",
            thedas_io_filter_package="",
            thedas_io_scan_addins=False,
            thedas_io_addins_dir="",
            thedas_io_scan_active=False,
            thedas_io_scan_progress=0,
            thedas_io_assets=FakeCollection(),
            thedas_io_index=0,
            thedas_io_page=page,
            thedas_io_assets_total=total,
            thedas_io_db_path=str(db),
        )
        for _ in range(n_items):
            scene.thedas_io_assets.add()
        panel = cast(object, _ui.THEDAS_IO_PT_import())
        panel.layout = FakeLayout()
        panel.draw(SimpleNamespace(scene=scene))
        return panel.layout

    small = draw_with(10, 10)
    assert "thedas_io.assets_page" not in small.operators

    big = draw_with(2501, 1000, page=1)
    assert big.operators.count("thedas_io.assets_page") == 2
    assert any("Page 2/3" in label for label in big.labels)
