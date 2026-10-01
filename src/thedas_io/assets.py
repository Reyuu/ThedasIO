# db row -> local file -> importer choice, no bpy

import tempfile
from pathlib import Path

from .asset_db.scanner import find_exact, payload

IMPORTERS = {"msh": "msh", "mmh": "mmh"}
_SESSION_DIR = None


def importer_for(ext):
    if not ext:
        return None
    return IMPORTERS.get(ext.lower().lstrip("."))


def _extract_dir():
    # one scratch dir per session so extracts don't pile up
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = Path(tempfile.mkdtemp(prefix="thedas_io_assets_"))
    return _SESSION_DIR


def extract(row, dest_dir=None):
    # archive entries go to temp, loose files just pass through
    src = Path(row["path"])
    if not row["package"]:
        return src
    if dest_dir is not None:
        dest = Path(dest_dir)
    else:
        dest = _extract_dir() / str(row["package"])
    out = dest / row["filename"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload(src, row["filename"]))
    return out


def resolve(name, row, db_path=None, local_only=False):
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

    hit = find_exact(name, db_path=db_path)
    if hit is None:
        return None
    return extract(hit) if hit["package"] else Path(hit["path"])
