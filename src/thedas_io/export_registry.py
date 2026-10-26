"""Export registry and scene sync: object rows, rename table, pruning."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import bpy

from .asset_browser import _scene_db
from .assets import resolve
from .datablocks import sweep_orphans
from .export import ITEMS, Item, _tnt_name, connected, remember, roots
from .materials import parse_mao_bytes
from .mmh.description import MMHReader
from .msh.description import MSHReader

_SYNCING_RENAMES = False
_SYNC_PENDING = False
_PRUNE_PENDING = False
_SWEEPING = False


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


def _prune_export_items(scene: Any) -> bool:
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


def _sync_export_items(scene: Any) -> None:
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


def _sync_rename_items(scene: Any) -> None:
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


def _sync_rename_later() -> None:
    global _SYNC_PENDING
    if _SYNC_PENDING:
        return
    _SYNC_PENDING = True
    try:
        bpy.app.timers.register(_sync_rename_timer, first_interval=0.2)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        _SYNC_PENDING = False


def _sync_rename_timer() -> None:
    # falling off the end returns None, which stops the one-shot timer
    global _SYNC_PENDING
    _SYNC_PENDING = False
    try:
        for one in bpy.data.scenes:
            _sync_rename_items(one)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _fill_rename_items(scene: Any) -> None:
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


def _push_renames(scene: Any) -> None:
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


def _stale_export_rows(scene: Any) -> bool:
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


def _prune_timer() -> None:
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


def _sweep_timer() -> None:
    # falling off the end returns None, which stops the one-shot timer
    global _SWEEPING
    _SWEEPING = False
    try:
        sweep_orphans()
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _on_depsgraph_update(scene: Any, depsgraph: Any = None) -> None:
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


def _safe_name(obj: Any) -> str:
    try:
        return obj.name if obj is not None else ""
    except (AttributeError, ReferenceError):
        return ""


def _store_registry(scene: Any) -> None:
    try:
        scene.thedas_io_registry = registry_blob()
    except (AttributeError, TypeError):
        pass


def rebuild_registry(scene: Any) -> list:
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

    def fetch(name: str) -> Any:
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
