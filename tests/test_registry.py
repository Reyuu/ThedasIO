"""Registry lifecycle: re-import supersedes, unregister clears, no leak.

The registry is module-level state shared by every export. These pin the two
ways it used to go wrong: source bytes that survived a re-import of an edited
file, and a registry that outlived the addon that filled it.
"""

from __future__ import annotations

from types import SimpleNamespace

from thedas_io.export import (
    _LOADED_ITEMS,
    ITEMS,
    export_bytes,
    remember,
    reset,
    roots,
)


def test_reimport_replaces_source_bytes():
    # An edited file re-imported must export the edit, not the first read.
    remember("a.msh", "msh", SimpleNamespace(name="first"), b"OLD")
    assert export_bytes(ITEMS["a.msh"]) == b"OLD"

    remember("a.msh", "msh", SimpleNamespace(name="second"), b"NEW")
    assert export_bytes(ITEMS["a.msh"]) == b"NEW"


def test_reimport_drops_stale_members():
    # A superseded model must not keep the previous one's Blender members.
    old = SimpleNamespace(name="old")
    item = remember("a.msh", "msh", old, b"v1")
    item["objects"].append(("stale", "chunk"))

    remember("a.msh", "msh", SimpleNamespace(name="new"), b"v2")
    assert ITEMS["a.msh"]["objects"] == []


def test_texture_reimport_does_not_grow():
    """Textures pass model=None, so 'latest wins' is what keeps the list short.

    export reads item['objects'][-1]; an ever-growing list would also keep every
    superseded image datablock alive for the session.
    """
    for i in range(5):
        remember("t_0d.dds", "texture", None, b"v%d" % i)["objects"].append(
            SimpleNamespace(name=f"img{i}")
        )
    assert len(ITEMS["t_0d.dds"]["objects"]) == 1
    assert ITEMS["t_0d.dds"]["objects"][-1].name == "img4"


def test_reset_clears_both_structures():
    # _LOADED_ITEMS holds the same dicts, so a partial clear leaks them.
    remember("a.msh", "msh", SimpleNamespace(), b"v")
    remember("b.mmh", "mmh", SimpleNamespace(), b"v")
    assert roots() and _LOADED_ITEMS

    reset()
    assert ITEMS == {}
    assert _LOADED_ITEMS == []
    assert roots() == []


def test_same_model_object_does_not_reset():
    """Repeated remember() with the same model is not a re-import.

    build_hierarchy registers a model once and then hands out members; a reset
    there would drop the objects it just created.
    """
    model = SimpleNamespace(name="same")
    item = remember("a.msh", "msh", model, b"v1")
    item["objects"].append(("keep", "chunk"))

    remember("a.msh", "msh", model, b"v1")
    assert ITEMS["a.msh"]["objects"] == [("keep", "chunk")]
