"""Export and check survive a single bad resource.

The user-visible promise is that one broken mesh does not cost them the other
eight files in the batch. Every re-encode hook raises in its own way --
`decode_chunk` raises `struct.error` on a blob it cannot read, blender raises
`ReferenceError` on deleted datablocks, `_TopologyChanged` is a `ValueError` --
so `export_all` and `check_model` catch broadly and name the type.
"""

from __future__ import annotations

import struct

import pytest

from thedas_io.export import _DIRTY, ITEMS, _normal_drift, check_model, export_all
from thedas_io.msh.description import Mesh

SRC_NORMAL = (0.0, 0.0, 1.0)


def test_a_normal_blender_cannot_represent_is_not_an_edit():
    # untouched meshes must come out byte-identical, so a live normal blender
    # could not reproduce must not read as a normal edit. some face meshes come
    # back with a zero, some with a partial vector; both are blender's answer,
    # not the modder's. a real edit lands a different *unit* vector.
    assert _normal_drift(SRC_NORMAL, SRC_NORMAL) is False
    assert _normal_drift((0.0, 0.0, 0.0), SRC_NORMAL) is False
    assert _normal_drift((0.12, -0.61, 0.005), SRC_NORMAL) is False  # |n| 0.62
    assert _normal_drift(SRC_NORMAL, (0.0, 0.0, 0.0)) is False  # source zero
    assert _normal_drift((0.98, 0.0, 0.2), SRC_NORMAL) is True  # a genuine edit


@pytest.fixture
def only_item():
    saved = dict(ITEMS)
    ITEMS.clear()
    yield ITEMS
    ITEMS.clear()
    ITEMS.update(saved)


def _item(name, kind="msh", raw=b"raw"):
    return {"file": name, "kind": kind, "model": None, "raw": raw, "objects": []}


def _boom(exc):
    def hook(item, tint, renames):
        raise exc

    return hook


def test_a_failing_file_does_not_take_the_batch_with_it(only_item, tmp_path):
    # struct.error is what the 13 over-65535-index meshes raise today
    only_item["bad.msh"] = _item("bad.msh", kind="boom")
    only_item["good.msh"] = _item("good.msh", kind="quiet")
    _DIRTY["boom"] = _boom(struct.error("unpack requires a buffer of 387180 bytes"))
    _DIRTY["quiet"] = lambda item, tint, renames: None
    try:
        written, errors = export_all(tmp_path)
    finally:
        del _DIRTY["boom"], _DIRTY["quiet"]

    assert written == ["good.msh"]
    assert (tmp_path / "good.msh").read_bytes() == b"raw"
    assert len(errors) == 1
    assert "bad.msh" in errors[0]


def test_unexpected_errors_name_their_type(only_item, tmp_path):
    # a bare KeyError tells the modder nothing; the type does
    only_item["bad.msh"] = _item("bad.msh", kind="boom")
    _DIRTY["boom"] = _boom(KeyError("thedas_io_bone_index"))
    try:
        _written, errors = export_all(tmp_path)
    finally:
        del _DIRTY["boom"]

    assert errors == ["bad.msh: KeyError: 'thedas_io_bone_index'"]


def test_actionable_failures_keep_their_plain_text(only_item, tmp_path):
    # "in Edit Mode" is something the modder can act on; do not bury it
    only_item["a.msh"] = _item("a.msh", kind="boom")
    _DIRTY["boom"] = _boom(ValueError("in Edit Mode - leave Edit Mode, then export"))
    try:
        _written, errors = export_all(tmp_path)
    finally:
        del _DIRTY["boom"]

    assert errors == ["a.msh: in Edit Mode - leave Edit Mode, then export"]


def test_check_model_reports_an_unreadable_chunk_instead_of_raising(only_item):
    # the vertex blob stops short of the rows 8001 promises, so the row decode
    # raises struct.error -- the failure mode that used to abort the check
    chunk = {
        2: "c",
        8000: 12,  # matches the FLOAT3 declarator below
        8001: 3,
        8002: 3,
        8003: 0,
        8006: 0,
        8009: 0,
        8025: [{8026: 0, 8027: 0, 8028: 2, 8029: 0, 8030: 0, 8031: 0}],
    }
    mesh = Mesh({2: "m", 8021: [chunk], 8022: [0] * 12, 8023: [0] * 6})
    only_item["a.msh"] = {
        "file": "a.msh",
        "kind": "msh",
        "model": mesh,
        "raw": b"",
        "objects": [],
    }

    notes = check_model()

    assert len(notes) == 1
    assert notes[0].startswith("a.msh: chunk c unreadable (error:")


def test_check_model_keeps_checking_the_rest_of_the_model(only_item):
    bad = _item("bad.msh", kind="msh")
    bad["model"] = None
    only_item["bad.msh"] = bad
    only_item["c.dds"] = _item("c.dds", kind="texture", raw=b"not an image")

    notes = check_model()

    assert any("unreadable image bytes" in n for n in notes)
