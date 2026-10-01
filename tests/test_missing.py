"""Missing-texture placeholders: magenta, tagged, never encoded.

_resolve_texture returns None for unresolvable ResNames; apply_materials
fills those slots with _missing_image stand-ins so the viewport gap is
visible. These pin the stand-in contract: magenta pixels, sibling size,
thedas_io_missing tag, and no registry/cache side effects (which is what keeps
DDS re-encode, Substance unpack, reload and rebuild-registry skipping it).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from thedas_io import ui
from thedas_io.export import ITEMS, reset


def _sized(w, h):
    return SimpleNamespace(size=(w, h))


def test_missing_image_is_magenta_tagged_and_sibling_sized():
    reset()
    before = dict(ITEMS)
    image = ui._missing_image("pf_arm_0d.dds", {"a": _sized(128, 64), "b": _sized(64, 64)})
    assert image.name == "pf_arm_0d.dds"
    assert list(image.size) == [128, 64]
    assert image.get("thedas_io_missing") == "pf_arm_0d.dds"
    writes = image.pixels.writes
    assert len(writes) == 1 and len(writes[0]) == 128 * 64 * 4
    flat = writes[0]
    assert all(v == 1.0 for v in flat[0::4])
    assert all(v == 0.0 for v in flat[1::4])
    assert all(v == 1.0 for v in flat[2::4])
    assert all(v == 1.0 for v in flat[3::4])
    assert dict(ITEMS) == before  # no texture ITEM registered


def test_missing_image_falls_back_64():
    reset()
    image = ui._missing_image("x_0n.dds", {})
    assert list(image.size) == [64, 64]
    assert image.get("thedas_io_missing") == "x_0n.dds"


def test_missing_image_writes_no_cache_file(tmp_path, monkeypatch):
    reset()
    before = sorted(p.name for p in tmp_path.iterdir())
    ui._missing_image("x_0d.dds", {})
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert Path(tmp_path / "thedas_io_textures").exists() is False
