"""Asset database browser: scan, search and page the games' assets."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import bpy

from .asset_db.scanner import _db_path, count, find, scan

ASSET_LIMIT = 1000


_UNLIMITED_CACHE: tuple | None = None


def default_addins_dir(game=None, game_dir=None) -> str:
    # where the installer drops DLC and mods, next to the game data
    game = (game or "DAO").upper()
    if game == "DA2":
        if game_dir:
            return str(Path(game_dir) / "addins")
        return ""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return str(Path(home) / "Documents" / "BioWare" / "Dragon Age" / "AddIns")


def _prefs_game_dir(context) -> str:
    game = "DAO"
    try:
        scene = getattr(context, "scene", None)
        if scene is not None:
            game = getattr(scene, "thedas_io_game", "DAO") or "DAO"
    except Exception:  # noqa: BLE001
        game = "DAO"
    game = str(game).upper()
    attr = "thedas_io_da2_game_dir" if game == "DA2" else "thedas_io_game_dir"
    try:
        addons = context.preferences.addons
    except AttributeError:
        return ""
    candidates = []
    for key in (__package__ or "", "thedas_io"):
        try:
            candidates.append(addons[key])
        except (KeyError, TypeError):
            continue
    try:
        candidates.extend(a for a in addons if all(a is not c for c in candidates))
    except TypeError:
        pass
    for addon in candidates:
        game_dir = getattr(getattr(addon, "preferences", None), attr, "") or ""
        if game_dir:
            return str(game_dir)
    return ""


def _invalidate_asset_limit(_self=None, _context=None) -> None:
    # the preference checkbox changed; the next draw recomputes it
    global _UNLIMITED_CACHE
    _UNLIMITED_CACHE = None


def _prefs_unlimited(context=None) -> bool:
    # old uncapped list, opt-in via preferences; default capped. Cached by the
    # preferences object: this runs from every panel draw, and walking the
    # add-on map each redraw was pure overhead
    ctx = context
    if ctx is None:
        try:
            ctx = bpy.context
        except Exception:  # noqa: BLE001 - fake bpy / headless
            return False
    try:
        addons = ctx.preferences.addons
    except Exception:  # noqa: BLE001
        return False
    global _UNLIMITED_CACHE
    for key in (__package__ or "", "thedas_io"):
        if not key:
            continue
        try:
            prefs = addons[key].preferences
        except Exception:  # noqa: BLE001, S112 - missing addon / fake mapping
            continue
        if _UNLIMITED_CACHE is not None and _UNLIMITED_CACHE[0] is prefs:
            return _UNLIMITED_CACHE[1]
        try:
            value = bool(getattr(prefs, "thedas_io_unlimited_assets", False))
        except Exception:  # noqa: BLE001, S112
            continue
        _UNLIMITED_CACHE = (prefs, value)
        return value
    return False


def _asset_limit(context=None):
    return None if _prefs_unlimited(context) else ASSET_LIMIT


def _page_count(total: int, limit) -> int:
    if limit is None or total <= 0:
        return 1
    return (total + limit - 1) // limit


def _asset_message(shown: int, total: int, page: int = 0, limit=None) -> str:
    if limit is None or total <= limit:
        return f"{total} assets"
    pages = _page_count(total, limit)
    page = max(0, min(page, pages - 1))
    start = page * limit + 1
    return f"{start}-{start + shown - 1}/{total} assets (page {page + 1}/{pages})"


def _scene_db(scene):
    # blank override means the per-game default; resolving it here keeps every
    # lookup on the same file as the asset list instead of falling back to DAO
    return _db_path(
        getattr(scene, "thedas_io_db_path", "") or None,
        getattr(scene, "thedas_io_game", "DAO"),
    )


def _fill_assets(
    scene,
    db_path=None,
    ext=None,
    name=None,
    package=None,
    limit="default",
    offset=0,
    context=None,
) -> tuple:
    if isinstance(ext, str):
        ext = ext.strip() or None
    if limit == "default":
        limit = _asset_limit(context)
    if limit is None:
        offset = 0
    else:
        offset = max(0, int(offset))
    try:
        total = count(ext, name, package, db_path, game=getattr(scene, "thedas_io_game", "DAO"))
    except sqlite3.Error:
        total = None
    try:
        rows = find(
            ext,
            name,
            package,
            db_path,
            limit=limit,
            offset=offset,
            game=getattr(scene, "thedas_io_game", "DAO"),
        )
    except sqlite3.Error:
        # a search pressed while the scan thread is writing must show an empty
        # page, not an operator traceback; the next refresh retries
        rows = []
    if total is None:
        total = len(rows)
    scene.thedas_io_assets.clear()

    for r in rows:
        item = scene.thedas_io_assets.add()
        item.package = r["package"] or ""
        item.filename = r["filename"]
        item.ext = r["ext"]
        item.path = r["path"]
        h = int(r.get("name_hash") or 0)
        item.name_hash = f"{h & 0xFFFFFFFFFFFFFFFF:016x}" if h else ""
    try:
        scene.thedas_io_assets_total = int(total)
        scene.thedas_io_page = offset // limit if limit else 0
        scene.thedas_io_index = 0
    except (AttributeError, ReferenceError, TypeError, ValueError):
        pass
    return rows, total


def _ext_filter(value) -> str | None:
    # none drops extension filter, find normalises case
    return None if not value or value == "NONE" else value


def _refresh_assets_page(context, scene=None, page=0) -> tuple:
    # one place that maps filters + page to a LIMIT/OFFSET slice
    scene = scene if scene is not None else context.scene
    limit = _asset_limit(context)
    ext = _ext_filter(getattr(scene, "thedas_io_filter_ext", "NONE"))
    name = getattr(scene, "thedas_io_filter_name", "") or None
    package = getattr(scene, "thedas_io_filter_package", "") or None
    if limit is None:
        page = 0
    else:
        try:
            total_hint = count(
                ext,
                name,
                package,
                _scene_db(scene),
            )
        except sqlite3.Error:
            total_hint = 0
        pages = _page_count(total_hint, limit)
        page = max(0, min(int(page), pages - 1))
    rows, total = _fill_assets(
        scene,
        _scene_db(scene),
        ext,
        name,
        package,
        limit=limit,
        offset=page * limit if limit else 0,
        context=context,
    )
    return rows, total, page


def _scan_async(root, db_path, game=None) -> dict:
    # scanner scan in worker thread, poll current/total/done

    state: dict = {
        "done": False,
        "current": 0,
        "total": 0,
        "stats": None,
        "error": None,
        "thread": None,
    }

    def work():
        try:

            def progress(done, total):
                state["current"] = done
                state["total"] = total

            state["stats"] = scan(root, db_path, progress=progress, game=game)
        except Exception as exc:  # noqa: BLE001
            # a whitelist here let anything else (MemoryError, RecursionError)
            # kill the thread silently, and the operator then read the missing
            # stats as a successful scan of an empty folder
            state["error"] = exc
        finally:
            state["done"] = True

    thread = threading.Thread(target=work, daemon=True)
    state["thread"] = thread
    thread.start()
    return state


class THEDAS_IO_OT_scan_assets(bpy.types.Operator):
    bl_idname = "thedas_io.scan_assets"
    bl_label = "Scan DAO packages"
    bl_description = (
        "Index every .rim/.erf archive plus loose models and "
        "textures under the scan folder into the asset database"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Scan here instead of the game folder for this run",
    )
    _timer = None
    _state = None
    _db_path = ""
    _scene = None

    def _resolve(self, context):
        # one or two roots: the game folder, plus the AddIns folder if asked
        roots = []
        root = bpy.path.abspath(getattr(self, "directory", "") or _prefs_game_dir(context))
        if not root:
            return [], "Set a scan folder or the game folder in Preferences"
        if not Path(str(root)).exists():
            return [], f"Scan folder not found: {root}"
        roots.append(str(root))

        scene = context.scene
        if getattr(scene, "thedas_io_scan_addins", False):
            addins = bpy.path.abspath(
                scene.thedas_io_addins_dir
                or default_addins_dir(
                    game=getattr(scene, "thedas_io_game", "DAO"), game_dir=_prefs_game_dir(context)
                )
            )
            if not Path(addins).is_dir():
                return [], f"AddIns folder not found: {addins}"
            if addins not in roots:
                roots.append(addins)
        return roots, ""

    def invoke(self, context, event) -> set:
        # scan in worker thread, progress via modal timer
        if getattr(context.scene, "thedas_io_scan_active", False):
            self.report({"WARNING"}, "A scan is already running")
            return {"CANCELLED"}

        roots, err = self._resolve(context)
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}

        scene = context.scene
        self._db_path = str(scene.thedas_io_db_path or "")
        if not self._db_path:
            self._db_path = str(_db_path(None, getattr(scene, "thedas_io_game", "DAO")))
        self._scene = scene
        self._state = _scan_async(
            roots, self._db_path or None, getattr(scene, "thedas_io_game", "DAO")
        )
        scene.thedas_io_scan_active = True
        scene.thedas_io_scan_progress = 0

        try:
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.1, window=context.window)
        except (AttributeError, RuntimeError, TypeError):
            self._state["thread"].join()  # no event loop: finish synchronously
            return self._finish(context)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context) -> set:
        # synchronous path for scripts, background mode, tests
        if getattr(context.scene, "thedas_io_scan_active", False):
            self.report({"WARNING"}, "A scan is already running")
            return {"CANCELLED"}

        roots, err = self._resolve(context)
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}

        self._db_path = str(context.scene.thedas_io_db_path or "")
        if not self._db_path:
            self._db_path = str(_db_path(None, getattr(context.scene, "thedas_io_game", "DAO")))
        self._scene = context.scene
        self._state = _scan_async(
            roots, self._db_path or None, getattr(context.scene, "thedas_io_game", "DAO")
        )
        self._state["thread"].join()
        return self._finish(context)

    def modal(self, context, event) -> set:
        if event.type == "ESC":
            self._cancel_timer(context)
            if self._scene is not None:
                self._scene.thedas_io_scan_active = False
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        return self._tick(context)

    def _tick(self, context) -> set:
        # one progress tick, callable without event pump
        state = self._state or {}
        scene = self._scene or context.scene
        total = state.get("total") or 0
        scene.thedas_io_scan_progress = int(100 * state.get("current", 0) / total) if total else 0

        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()

        if not state.get("done"):
            return {"RUNNING_MODAL"}
        self._cancel_timer(context)
        return self._finish(context)

    def _finish(self, context) -> set:
        scene = self._scene or context.scene
        scene.thedas_io_scan_active = False
        state = self._state or {}
        if state.get("error") is not None:
            self.report({"WARNING"}, f"Scan failed: {state['error']}")
            return {"CANCELLED"}

        # the worker catches everything now, so no stats at all means it died
        # before returning. that is a failed scan, not a folder holding nothing,
        # and reporting it as success cleared the asset list for nothing
        if state.get("stats") is None:
            self.report({"WARNING"}, "Scan failed: the worker stopped early")
            return {"CANCELLED"}

        rows, total, page = _refresh_assets_page(context, scene, page=0)
        stats = state.get("stats") or {}
        notes = []
        if stats.get("missing"):
            notes.append(f"skipped {len(stats['missing'])} missing folder(s)")
        if stats.get("skipped"):
            # an archive that would not parse still counts in `files`, so
            # without this it looks like a folder that simply held nothing
            notes.append(f"{len(stats['skipped'])} unreadable archive(s)")
        note = ", ".join(notes)
        self.report(
            {"INFO"},
            f"Scanned {stats.get('files', 0)} archive files, "
            f"{stats.get('entries', 0)} entries, "
            f"{_asset_message(len(rows), total, page, _asset_limit(context))}. {note}".strip(),
        )
        return {"FINISHED"}

    def _cancel_timer(self, context):
        wm = getattr(context, "window_manager", None)
        if wm is not None and self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except (AttributeError, RuntimeError):
                pass
            self._timer = None

    def cancel(self, context):
        self._cancel_timer(context)
        if self._scene is not None:
            self._scene.thedas_io_scan_active = False


class THEDAS_IO_OT_refresh_assets(bpy.types.Operator):
    bl_idname = "thedas_io.refresh_assets"
    bl_label = "Refresh DAO assets"
    bl_description = (
        "Re-run the extension/name/package filters against the "
        "asset database without rescanning the archives"
    )

    def execute(self, context) -> set:
        rows, total, page = _refresh_assets_page(context, page=0)

        self.report({"INFO"}, _asset_message(len(rows), total, page, _asset_limit(context)))
        return {"FINISHED"}


class THEDAS_IO_OT_assets_page(bpy.types.Operator):
    bl_idname = "thedas_io.assets_page"
    bl_label = "Asset page"
    bl_description = "Show the previous or next page of the asset list"
    step: bpy.props.IntProperty(default=1)  # type: ignore

    def execute(self, context) -> set:
        scene = context.scene
        if _asset_limit(context) is None:
            self.report({"INFO"}, "All assets already shown")
            return {"FINISHED"}
        current = getattr(scene, "thedas_io_page", 0) or 0
        try:
            want = int(current) + int(getattr(self, "step", 1) or 0)
        except (TypeError, ValueError):
            return {"CANCELLED"}
        rows, total, page = _refresh_assets_page(context, scene, page=want)
        limit = _asset_limit(context)
        self.report({"INFO"}, _asset_message(len(rows), total, page, limit))
        return {"FINISHED"}
