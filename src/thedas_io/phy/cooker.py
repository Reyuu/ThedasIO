from __future__ import annotations

import sqlite3
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).with_name("cook.cs")
HELPER = Path(__file__).with_name("cook.exe")
MAGIC = b"NXS\x01MESH"
DEFAULTS = {"edge": 0.001, "axis": 0xFF, "extent": 0.0}
TIMEOUT = 120


# cooking could not run, or the library refused the mesh
class CookerError(RuntimeError):
    pass


# the game's cooking library: DAO ships nxcooking.dll, DA2 physxcooking.dll
def game_dll(game_dir: Any = None, db_path: Any = None, game: Any = None) -> Path | None:
    da2 = str(game or "").upper() == "DA2"
    names = ("physxcooking.dll", "nxcooking.dll") if da2 else ("nxcooking.dll", "physxcooking.dll")
    if game_dir is not None:
        # bin_ship, or a flattened copy beside the game folder
        for name in names:
            for candidate in (
                Path(game_dir) / "bin_ship" / name,
                Path(game_dir) / name,
            ):
                if candidate.is_file():
                    return candidate
    if db_path is not None:
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "select path from assets where path is not null limit 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        if row and row[0]:
            folder = Path(row[0])
            # asset paths sit a few levels under the game folder
            for _ in range(6):
                folder = folder.parent
                for name in names:
                    candidate = folder / "bin_ship" / name
                    if candidate.is_file():
                        return candidate
    return None


def available(game_dir: Any = None, db_path: Any = None, game: Any = None) -> bool:
    return HELPER.is_file() and game_dll(game_dir, db_path, game) is not None


def context_paths(context: Any = None) -> tuple:
    # where the add-on's scene/prefs say the game, its asset db and folder live
    scene = getattr(context, "scene", None)
    game = str(getattr(scene, "thedas_io_game", "DAO") or "DAO").upper()
    db = None
    try:
        from ..asset_db.scanner import _db_path

        db = _db_path(
            getattr(scene, "thedas_io_db_path", "") or None,
            game,
        )
    except Exception:  # noqa: BLE001 - no scene/db is not fatal here
        db = None
    game_dir = None
    try:
        from ..asset_browser import _prefs_game_dir

        game_dir = _prefs_game_dir(context) or None
    except Exception:  # noqa: BLE001
        game_dir = None
    return game_dir, db, game


def cook_in_context(verts: Any, tris: Any, context: Any = None) -> bytes:
    # cook with the game/db the current Blender context points at
    game_dir, db, game = context_paths(context)
    return cook_triangle_mesh(verts, tris, game_dir=game_dir, db_path=db, game=game)


def cook_triangle_mesh(
    verts: Any,
    tris: Any,
    *,
    game_dir: Any = None,
    db_path: Any = None,
    game: Any = None,
    edge: float = DEFAULTS["edge"],
    axis: int = DEFAULTS["axis"],
    extent: float = DEFAULTS["extent"],
) -> bytes:
    # verts are (x, y, z), tris are (a, b, c) indices
    # raises CookerError when cook.exe, the library, or PhysX fails
    if not HELPER.is_file():
        raise CookerError("cook.exe is missing from the add-on; reinstall it")
    dll = game_dll(game_dir, db_path, game)
    if dll is None:
        raise CookerError(
            "nxcooking.dll/physxcooking.dll not found: point the addon at the game folder"
        )

    verts = list(verts)
    tris = list(tris)
    lines = [f"{len(verts)} {len(tris)}"]
    lines.extend("{:.9g} {:.9g} {:.9g}".format(*map(float, v)) for v in verts)
    lines.extend("{} {} {}".format(*map(int, t)) for t in tris)

    work = Path(tempfile.mkdtemp(prefix="thedas_io_cook_"))
    try:
        mesh = work / "mesh.txt"
        mesh.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = work / "cooked.bin"
        result = subprocess.run(
            [
                str(HELPER),
                str(dll),
                str(mesh),
                str(out),
                repr(float(edge)),
                str(int(axis)),
                repr(float(extent)),
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not out.is_file():
            raise CookerError(f"cooking failed: {(result.stderr or result.stdout).strip()}")
        cooked = out.read_bytes()
    finally:
        for leftover in work.glob("*"):
            leftover.unlink(missing_ok=True)
        work.rmdir()

    if not cooked.startswith(MAGIC):
        raise CookerError("the library returned something that is not an NXS MESH stream")
    return cooked


# the cooking parameters a shipped stream was made with
def stream_params(raw: bytes) -> dict:
    # the three values sit in the stream header, right after the sizes
    edge = struct.unpack_from("<f", raw, 16)[0]
    axis = struct.unpack_from("<I", raw, 20)[0]
    extent = struct.unpack_from("<f", raw, 24)[0]
    return {"edge": edge, "axis": axis, "extent": extent}
