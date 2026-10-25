# db row -> local file -> importer choice, no bpy

import atexit
import shutil
import tempfile
from pathlib import Path

from .asset_db.scanner import find_exact, payload

IMPORTERS = {"msh": "msh", "mmh": "mmh"}
_SESSION_DIR = None


def importer_for(ext):
    if not ext:
        return None
    return IMPORTERS.get(ext.lower().lstrip("."))


def _safe_name(name) -> str:
    # archive entry and package names are archive-controlled. a crafted
    # `..\..\evil.dll` must land as `evil.dll` under the extraction dir, never
    # above it
    leaf = Path(str(name).replace("\\", "/")).name
    if not leaf or leaf in (".", "..") or "\x00" in leaf or Path(leaf).drive:
        raise ValueError(f"archive entry has no safe file name: {name!r}")
    return leaf


def _extract_dir():
    # one scratch dir per session so extracts don't pile up
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = Path(tempfile.mkdtemp(prefix="thedas_io_assets_"))
        atexit.register(cleanup_session)
    return _SESSION_DIR


def cleanup_session() -> None:
    # drop the scratch dir when the add-on is disabled or Blender exits
    global _SESSION_DIR
    directory, _SESSION_DIR = _SESSION_DIR, None
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)


def extract(row, dest_dir=None):
    # archive entries go to temp, loose files just pass through
    src = Path(row["path"])
    if not row["package"]:
        return src
    name = _safe_name(row["filename"])
    if dest_dir is not None:
        dest = Path(dest_dir)
    else:
        dest = _extract_dir() / _safe_name(row["package"])
    out = dest / name
    out.parent.mkdir(parents=True, exist_ok=True)
    # the archive lookup still uses the entry's own name, only the file on disk
    # is renamed
    out.write_bytes(payload(src, row["filename"], row.get("name_hash")))
    return out


def resolve(name, row, db_path=None, local_only=False, game=None):
    # same dir first, then db, unless local_only
    if not name:
        return None
    if not row["package"]:
        local = Path(row["path"]).parent / name
        if local.is_file():
            return local
        if local_only:
            return None
    elif local_only:
        return None

    hit = find_exact(name, db_path=db_path, game=game)
    if hit is None:
        return None
    return extract(hit) if hit["package"] else Path(hit["path"])
