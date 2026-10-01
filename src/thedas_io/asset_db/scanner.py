import os
import sqlite3
import struct
from pathlib import Path

from ..erf.description import ERFReader

SCAN_EXTS = {".rim", ".erf"}
LOOSE_EXTS = {".msh", ".mmh", ".mao", ".dds", ".tga"}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS assets(package, filename, ext, path);"
    "CREATE INDEX IF NOT EXISTS idx_assets_ext_name ON assets(ext, filename);"
)


def _db_path(path=None):
    if path is not None:
        return Path(path)
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return Path(base) / "BlenderDAOTools" / "asset_db.sqlite"


def _entry_table(path):
    path = Path(path)
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return _read_entry_table(path)

    hit = _ENTRY_TABLES.get(key)
    if hit is None:
        hit = _read_entry_table(path)
        _ENTRY_TABLES.clear()
        _ENTRY_TABLES[key] = hit
    return hit


# just the newest table, keyed on path+mtime+size
_ENTRY_TABLES: dict[tuple[str, int, int], bytes] = {}


def _read_entry_table(path):
    with open(path, "rb") as f:
        head = f.read(32)
        count = struct.unpack("<I", head[16:20])[0]
        if count > 1000000:
            raise ValueError(f"implausible entry count {count}")
        rest = f.read(count * 72)
    return head + rest


def _norm_ext(ext):
    if ext is None:
        return None
    ext = str(ext).strip().lower()
    if not ext:
        return None
    if ext.startswith("."):
        return ext
    return f".{ext}"


def scan(root, db_path=None, progress=None):
    root = Path(root)
    db = _db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM assets")

    files = 0
    entries = 0
    skipped = []
    targets = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in SCAN_EXTS or p.suffix.lower() in LOOSE_EXTS:
            targets.append(p)

    total = len(targets)
    for i, path in enumerate(targets):
        suffix = path.suffix.lower()
        if suffix not in SCAN_EXTS:
            con.execute("INSERT INTO assets VALUES (?,?,?,?)", (None, path.name, suffix, str(path)))
        else:
            files += 1
            # only the contents, not the archive itself
            rows = []
            try:
                package = []
                for e in ERFReader(_entry_table(path)).entries:
                    package.append((path.name, e.name, Path(e.name).suffix.lower(), str(path)))
            except (OSError, ValueError, struct.error):
                skipped.append(str(path))
            else:
                rows = package
                entries += len(package)
            con.executemany("INSERT INTO assets VALUES (?,?,?,?)", rows)
        if progress is not None:
            progress(i + 1, total)
    con.commit()
    con.close()
    return {"files": files, "entries": entries, "skipped": skipped}


def payload(archive, name):
    archive = Path(archive)
    for e in ERFReader(_entry_table(archive)).entries:
        if e.name == name:
            with open(archive, "rb") as f:
                f.seek(e.offset)
                return f.read(e.size)
    raise KeyError(f"entry '{name}' not found in '{archive.name}'")


def find(ext=None, name=None, package=None, db_path=None):
    db = _db_path(db_path)
    if not db.exists():
        return []
    ext = _norm_ext(ext)
    clauses = []
    params = []
    if ext is not None:
        clauses.append("ext = ?")
        params.append(ext.lower())
    if name is not None:
        clauses.append("filename LIKE ?")
        params.append(f"%{name}%")
    if package is not None:
        clauses.append("package = ?")
        params.append(package)

    where = ""
    if clauses:
        where = f" WHERE {' AND '.join(clauses)}"

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out = []
    for r in con.execute(f"SELECT package, filename, ext, path FROM assets{where}", params):
        out.append(dict(r))
    con.close()
    return out


def find_exact(name, db_path=None):
    # loose files first so we skip extracting, then package name for stable picks
    if not name:
        return None
    db = _db_path(db_path)
    if not db.exists():
        return None

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT package, filename, ext, path FROM assets "
            "WHERE ext = ? AND filename = ? COLLATE NOCASE "
            "ORDER BY package IS NOT NULL, package, path LIMIT 1",
            (_norm_ext(Path(name).suffix), name),
        ).fetchone()
    finally:
        con.close()

    if row is not None:
        return dict(row)
    return None


def load(db_path=None):
    return find(db_path=db_path)
