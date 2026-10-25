"""Blender datablock ownership: tag what the add-on creates, free it when orphaned."""

from __future__ import annotations

from typing import Any

import bpy


def tag(block: Any, kind: str) -> None:
    # the sweep below must never free a datablock the user made
    try:
        block["thedas_io"] = kind
    except (AttributeError, TypeError, ValueError):
        pass


def sweep_orphans() -> int:
    # deleting an imported object leaves its mesh, material and packed textures
    # with no users but still resident for the whole session. Importing a few
    # models then dropping them grows Blender by ~100MB per character until it
    # swaps and crashes. Free our own zero-user datablocks only: they are tagged
    # at creation, so nothing the user made is touched.
    collections: list = [
        getattr(bpy.data, name, ())
        for name in ("meshes", "armatures", "materials", "node_groups", "images")
    ]
    removed = 0
    for _ in range(2):
        pass_removed = 0
        for blocks in collections:
            try:
                listing = list(blocks)
            except (AttributeError, TypeError):
                continue
            for block in listing:
                try:
                    if block.users == 0 and block.get("thedas_io"):
                        blocks.remove(block)
                        pass_removed += 1
                except (AttributeError, ReferenceError, TypeError, RuntimeError):
                    continue
        removed += pass_removed
        if not pass_removed:
            break
    return removed
