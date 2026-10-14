import os
import sqlite3
import struct
from pathlib import Path

from ..erf.description import ERFReader, geometry

SCAN_EXTS = {".rim", ".erf"}
LOOSE_EXTS = {".msh", ".mmh", ".mao", ".dds", ".tga"}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS assets(package, filename, ext, path, prio);"
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
    # header + entry_count rows, all of which have to fit in the file. the
    # bound comes from the file rather than a guess: a truncated or foreign
    # archive reports a garbage count, and reading count * row bytes would then
    # try to allocate up to 309GB and raise MemoryError mid-scan.
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        # 56B covers the longest header (V2.2); the magic says how much of it
        # is really header.
        head = f.read(56)
        header, row = geometry(head[:16])
        count = struct.unpack("<I", head[16:20])[0]
        if len(head) < header or header + count * row > size:
            raise ValueError(f"entry count {count} does not fit in {size} bytes")
        f.seek(0)
        return f.read(header + count * row)


def _norm_ext(ext):
    if ext is None:
        return None
    ext = str(ext).strip().lower()
    if not ext:
        return None
    if ext.startswith("."):
        return ext
    return f".{ext}"


def _priority(path, root, index: int) -> int:
    # higher wins when two archives hold the same file name. roots come in load
    # order, so a later root (AddIns, which the engine loads after the base
    # game) beats an earlier one; within a root the deeper path wins, and an
    # override/ folder wins outright because that is the last thing loaded.
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        parts = Path(path).parts
    prio = index * 1000 + len(parts)
    if any(p.lower() == "override" for p in parts):
        prio += 100
    return prio


def scan(root, db_path=None, progress=None):
    roots = [Path(root)] if isinstance(root, (str, Path)) else [Path(r) for r in root]
    db = _db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(assets)")}
    if "prio" not in cols:  # a database written before the column existed
        con.execute("ALTER TABLE assets ADD COLUMN prio INTEGER")
    con.execute("DELETE FROM assets")

    files = 0
    entries = 0
    skipped = []
    missing = []
    targets = []
    for i, r in enumerate(roots):
        if not r.is_dir():
            missing.append(str(r))
            continue
        for p in sorted(r.rglob("*")):
            if p.is_file() and p.suffix.lower() in SCAN_EXTS | LOOSE_EXTS:
                targets.append((p, r, i))

    total = len(targets)
    for i, (path, root_i, index) in enumerate(targets):
        suffix = path.suffix.lower()
        prio = _priority(path, root_i, index)
        if suffix not in SCAN_EXTS:
            con.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?)",
                (None, path.name, suffix, str(path), prio),
            )
        else:
            files += 1
            # only the contents, not the archive itself
            rows = []
            try:
                package = []
                for e in ERFReader(_entry_table(path)).entries:
                    package.append(
                        (path.name, e.name, Path(e.name).suffix.lower(), str(path), prio)
                    )
            except (OSError, ValueError, struct.error):
                skipped.append(str(path))
            else:
                rows = package
                entries += len(package)
            con.executemany("INSERT INTO assets VALUES (?,?,?,?,?)", rows)
        if progress is not None:
            progress(i + 1, total)
    con.commit()
    con.close()
    return {
        "files": files,
        "entries": entries,
        "skipped": skipped,
        "missing": missing,
    }


def payload(archive, name):
    archive = Path(archive)
    reader = ERFReader(_entry_table(archive))
    entry = next((e for e in reader.entries if e.name == name), None)
    if entry is None:
        raise KeyError(f"entry '{name}' not found in '{archive.name}'")
    # the reader works on the bytes we hand it, so the seek stays here
    with open(archive, "rb") as f:
        f.seek(entry.offset)
        return reader.decode_payload(f.read(entry.packed_size), entry)


def _where(ext=None, name=None, package=None):
    clauses = []
    params: list = []
    if ext is not None:
        clauses.append("ext = ?")
        params.append(ext.lower())
    if name is not None:
        clauses.append("filename LIKE ?")
        params.append(f"%{name}%")
    if package is not None:
        clauses.append("package = ?")
        params.append(package)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def count(ext=None, name=None, package=None, db_path=None) -> int:
    db = _db_path(db_path)
    if not db.exists():
        return 0
    where, params = _where(_norm_ext(ext), name, package)
    con = sqlite3.connect(db)
    try:
        row = con.execute(f"SELECT COUNT(*) FROM assets{where}", params).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def find(ext=None, name=None, package=None, db_path=None, limit=None, offset=0):
    db = _db_path(db_path)
    if not db.exists():
        return []
    if limit is not None:
        limit = int(limit)
        offset = int(offset)
        if limit < 0 or offset < 0:
            raise ValueError("limit/offset must be >= 0")
    where, params = _where(_norm_ext(ext), name, package)

    # rowid order = scan insertion order, so pages are stable
    sql = f"SELECT package, filename, ext, path FROM assets{where} ORDER BY rowid"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
    return out


def find_exact(name, db_path=None):
    # loose files first so we skip extracting, then the latest-loaded package
    # so a player's override beats the stock data it shadows
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
            "ORDER BY package IS NOT NULL, prio DESC, package, path LIMIT 1",
            (_norm_ext(Path(name).suffix), name),
        ).fetchone()
    finally:
        con.close()

    if row is not None:
        return dict(row)
    return None


def load(db_path=None):
    return find(db_path=db_path)
