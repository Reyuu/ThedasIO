import mmap
import os
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..erf.description import MAGIC_V3, ERFReader, geometry

SCAN_EXTS = {".rim", ".erf", ".crf", ".rimp"}
LOOSE_EXTS = {".msh", ".mmh", ".mao", ".dds", ".tga"}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS assets(package, filename, ext, path, prio, name_hash, resource);"
    "CREATE INDEX IF NOT EXISTS idx_assets_ext_name ON assets(ext, filename);"
)


@contextmanager
def _mapped(path: Any) -> Any:
    # read_bytes() on the biggest DA2 archive is an 870MB spike; mmap lets the
    # OS page it instead and the reader only slices what it needs.
    with open(path, "rb") as fh:
        try:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            yield fh.read()
            return
        try:
            yield mm
        finally:
            mm.close()


def fnv1(text: str) -> int:
    # FNV-1 64-bit: multiply, then xor. ERF V3 (DA2) hashes names this way.
    h = 0xCBF29CE484222325  # prime start
    for b in text.encode():
        h = (h * 0x100000001B3 ^ b) & 0xFFFFFFFFFFFFFFFF
    return h


def _s64(h: int) -> int:
    # SQLite INTEGER is signed 64-bit; fold unsigned FNV hashes into range.
    return h - (1 << 64) if h >= (1 << 63) else h


def _ensure_columns(con: sqlite3.Connection) -> None:
    # databases written by older builds lack these; add them before any SELECT
    cols = {row[1] for row in con.execute("PRAGMA table_info(assets)")}
    for name, decl in (
        ("prio", "INTEGER"),
        ("name_hash", "INTEGER"),
        ("resource", "TEXT"),
    ):
        if name not in cols:
            con.execute(f"ALTER TABLE assets ADD COLUMN {name} {decl}")


def _db_path(path: Any = None, game: str | None = None) -> Path:
    if path is not None:
        return Path(path)
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    game = (game or "DAO").strip().upper()
    if game not in ("DAO", "DA2"):
        game = "DAO"
    return Path(base) / "BlenderDAOTools" / f"asset_db_{game.lower()}.sqlite"


def _connect(db: Any) -> sqlite3.Connection:
    # the scan runs on a worker thread, so a reader must not fail with
    # "database is locked": WAL (set at scan time) lets it use its snapshot
    # while a write is in flight, and the timeout covers a checkpoint
    con = sqlite3.connect(db, timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _entry_table(path: Any) -> bytes:
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


# magic, then the u32 at 16 and the one at 20
_LEAD = struct.Struct("<16sII")


def _read_entry_table(path: Any) -> bytes:
    # header + entry_count rows, all of which have to fit in the file. the
    # bound comes from the file rather than a guess: a truncated or foreign
    # archive reports a garbage count, and reading count * row bytes would then
    # try to allocate up to 309GB and raise MemoryError mid-scan.
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        # only the magic and the two u32 are needed to size the table; the rows
        # themselves are re-read below.
        head = f.read(_LEAD.size)
        if len(head) < _LEAD.size:
            raise ValueError(f"truncated ERF header ({len(head)} of {_LEAD.size}B)")
        magic, field16, field20 = _LEAD.unpack(head)
        if magic == MAGIC_V3:
            header, row, count = 48 + field16, 28, field20
        else:
            header, row = geometry(magic)
            count = field16
        if header + count * row > size:
            raise ValueError(f"entry count {count} does not fit in {size} bytes")
        f.seek(0)
        return f.read(header + count * row)


def _norm_ext(ext: Any) -> str | None:
    if ext is None:
        return None
    ext = str(ext).strip().lower()
    if not ext:
        return None
    if ext.startswith("."):
        return ext
    return f".{ext}"


def _priority(path: Any, root: Any, index: int) -> int:
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


_DA2_TYPE_EXTS = None


def _da2_type_ext(entry: Any) -> str:
    # ERF V3 (DA2) stores fnv32 of the lowercased extension as the entry's
    # type_hash, so the real extension is known without decompressing it
    global _DA2_TYPE_EXTS
    if _DA2_TYPE_EXTS is None:
        from .da2_data import type_exts

        _DA2_TYPE_EXTS = type_exts()
    return _DA2_TYPE_EXTS.get(getattr(entry, "type_hash", 0), "")


def _synthetic_entry_name(entry: Any, reader: Any) -> str:
    typed = _da2_type_ext(entry)
    if typed:
        return f"hash_{entry.name_hash:016x}{typed}"
    ext = ".bin"
    try:
        raw = reader.data[entry.offset : entry.offset + entry.packed_size]
        plain = reader.decode_payload(raw, entry)
    except (OSError, ValueError, struct.error, Exception):  # noqa: BLE001
        plain = b""
    if plain[:4] == b"DDS ":
        ext = ".dds"
    elif len(plain) >= 8 and plain[3:4] == b" ":
        kind = plain[:4].split(b" ")[0].strip()
        if kind == b"GFF" and len(plain) >= 16:
            ft = plain[12:16]
            ext = {
                b"MESH": ".msh",
                b"MMH ": ".mmh",
                b"MAO ": ".mao",
                b"PHY ": ".phy",
                b"ANI ": ".ani",
                b"ANBT": ".anb",
                b"EVT ": ".evt",
                b"GAD ": ".gad",
                b"LINE": ".cl",
                b"MOR ": ".mor",
                b"RML ": ".rml",
                b"CUB ": ".cub",
                b"TNT ": ".tnt",
            }.get(ft, ".gff")
        elif kind and kind.isalnum():
            ext = "." + kind.decode("ascii").lower()
    return f"hash_{entry.name_hash:016x}{ext}"


def _archive_rows(path: Any, prio: int) -> list:
    # only the contents, not the archive itself
    with _mapped(path) as data:
        # guard against a garbage entry count before allocating
        if data[:16] == MAGIC_V3:
            string_table_size = struct.unpack("<I", data[16:20])[0]
            count = struct.unpack("<I", data[20:24])[0]
            header = 48 + string_table_size
            row = 28
        else:
            header, row = geometry(data[:16])
            count = struct.unpack("<I", data[16:20])[0]
        if header + count * row > len(data):
            raise ValueError("entry count does not fit in file")
        reader = ERFReader(data)
        rows = []
        for e in reader.entries:
            name = e.name or _synthetic_entry_name(e, reader)
            rows.append(
                (
                    path.name,
                    name,
                    Path(name).suffix.lower(),
                    str(path),
                    prio,
                    _s64(getattr(e, "name_hash", 0)),
                    name if ("\\" in name or "/" in name) else "",
                )
            )
        return rows


def scan(root: Any, db_path: Any = None, progress: Any = None, game: str | None = None) -> dict:
    roots = [Path(root)] if isinstance(root, (str, Path)) else [Path(r) for r in root]
    db = _db_path(db_path, game)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = _connect(db)
    try:
        try:
            # a plain delete journal makes a reader wait for the whole scan
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(assets)")}
        if "prio" not in cols:  # a database written before the column existed
            con.execute("ALTER TABLE assets ADD COLUMN prio INTEGER")
        if "name_hash" not in cols:  # a database written before DA2 hash indexing
            con.execute("ALTER TABLE assets ADD COLUMN name_hash INTEGER")
        if "resource" not in cols:  # full internal path, recovered from MMH 6005
            con.execute("ALTER TABLE assets ADD COLUMN resource TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_assets_namehash ON assets(name_hash)")
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
                    "INSERT INTO assets(package, filename, ext, path, prio, name_hash, resource) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (None, path.name, suffix, str(path), prio, 0, path.name),
                )
            else:
                files += 1
                try:
                    rows = _archive_rows(path, prio)
                except (OSError, ValueError, struct.error, IndexError, KeyError):
                    # one unreadable archive is reported, never fatal
                    skipped.append(str(path))
                    rows = []
                entries += len(rows)
                con.executemany(
                    "INSERT INTO assets(package, filename, ext, path, prio, name_hash, resource) "
                    "VALUES (?,?,?,?,?,?,?)",
                    rows,
                )
            if progress is not None:
                progress(i + 1, total)
        # DA2 archives store FNV-1 path hashes, not filenames. Name
        # recovery: walk every MMH entry, read its label 6005 (a real path such
        # as 'art\\vfx\\models\\vfx_shield_uhm.msh'), map FNV-1(path) -> basename,
        # and rename the matching mesh (etc.) entries so the asset list shows
        # real names.
        needs_paths = con.execute(
            "SELECT 1 FROM assets WHERE ext='.mmh' AND (resource IS NULL OR resource='') LIMIT 1"
        ).fetchone()
        _recover_names(con, game, force=needs_paths is not None)
        con.commit()
    finally:
        # an error mid-scan must not leave the connection (and its DELETE)
        # hanging: closing rolls the transaction back, so the old index survives
        from .da2_data import unload

        unload()  # the precomputed name table is only needed while indexing
        con.close()
    return {
        "files": files,
        "entries": entries,
        "skipped": skipped,
        "missing": missing,
    }


def _recover_names(con: sqlite3.Connection, game: str | None = None, force: bool = False) -> None:
    # DA2 only, since DAO stores real filenames in its archives. Names come from
    # three places, in this order: the MMH label 6005 (the real mesh path), the
    # bundled wordlist, then names recovered offline. The last two are already
    # hashed by tools/gen_da2_hashes.py, so a scan never runs FNV-1 over the
    # 279k-path wordlist itself.
    if str(game or "").upper() != "DA2":
        return

    unresolved = con.execute(
        "SELECT DISTINCT name_hash FROM assets WHERE filename LIKE 'hash_%' "
        "AND name_hash IS NOT NULL AND name_hash != 0"
    ).fetchall()
    if not unresolved and not force:
        return

    from ..mmh.description import MMHReader
    from .da2_data import names

    by_archive: dict = {}
    for rowid, filename, ext, path, package, name_hash in con.execute(
        "SELECT rowid, filename, ext, path, package, name_hash FROM assets "
        "WHERE ext='.mmh' AND name_hash IS NOT NULL AND name_hash != 0"
    ):
        by_archive.setdefault(path, []).append((rowid, filename, name_hash))

    name_map: dict = {}  # mesh name_hash -> real basename
    path_map: dict = {}  # mesh name_hash -> full internal path
    # MMH label 6005 paths
    for path, rows in by_archive.items():
        try:
            with _mapped(path) as data:
                reader = ERFReader(data)
                # DB rows hold signed hashes, the reader keeps the unsigned value
                by_hash = {_s64(getattr(e, "name_hash", 0)): e for e in reader.entries}
                for _rowid, _filename, name_hash in rows:
                    e = by_hash.get(name_hash)
                    if e is None:
                        continue
                    raw = data[e.offset : e.offset + e.packed_size]
                    try:
                        plain = reader.decode_payload(raw, e)
                    except (OSError, ValueError, KeyError, IndexError, struct.error):
                        continue
                    try:
                        tree = MMHReader(plain).tree
                    except (OSError, ValueError, KeyError, IndexError, struct.error):
                        continue
                    v = tree.values.get(6005)
                    if not isinstance(v, str) or not v:
                        continue
                    leaf = v.replace("\\", "/").rsplit("/", 1)[-1]
                    if not leaf or "." not in leaf:
                        continue
                    # label 6005 is the mesh path, but mmh/phy/mao siblings share
                    # its stem, so hash each candidate too: the exact hash
                    # decides, and the ext check below keeps a guess from naming
                    # the wrong kind of row.
                    stem = v[:-4] if v.lower().endswith(".msh") else v.rsplit(".", 1)[0]
                    for cand in (v, stem + ".mmh", stem + ".phy", stem + ".mao"):
                        h = _s64(fnv1(cand))
                        full = cand.replace("\\", "/")
                        name_map.setdefault(h, full.rsplit("/", 1)[-1])
                        path_map.setdefault(h, full)
        except (OSError, ValueError, struct.error, IndexError, KeyError):
            continue

    bundled = None  # only decompress the 340k-entry table when a hash needs it

    # only rename rows still synthetic, and do it in one statement: one UPDATE
    # per wordlist entry made large DA2 scans crawl
    # an exact 64-bit hash match is authoritative: the recovered name also
    # supplies the real extension, which the payload sniff could not always
    # derive (a .mor/.tnt/.cnv payload also parses as plain '.gff').
    hits = []
    seen = set()
    for (h,) in unresolved:
        hit_leaf: Any = name_map.get(h)
        if hit_leaf is None:
            if bundled is None:
                bundled = names()
            hit_leaf = bundled.get(h)
        if hit_leaf is not None:
            hits.append((h, hit_leaf, Path(hit_leaf).suffix.lower(), path_map.get(h, "")))
            seen.add(h)
    # already-named rows still need their full path on a backfill run
    for h, full in path_map.items():
        if h not in seen:
            leaf = full.rsplit("/", 1)[-1]
            hits.append((h, leaf, Path(leaf).suffix.lower(), full))
    con.execute("DROP TABLE IF EXISTS _recovered")
    con.execute(
        "CREATE TEMP TABLE _recovered(h INTEGER PRIMARY KEY, leaf TEXT, ext TEXT, resource TEXT)"
    )
    con.executemany("INSERT OR REPLACE INTO _recovered VALUES (?,?,?,?)", hits)
    con.execute(
        "UPDATE assets SET "
        "filename = (SELECT leaf FROM _recovered WHERE h = name_hash), "
        "ext = (SELECT ext FROM _recovered WHERE h = name_hash) "
        "WHERE name_hash IN (SELECT h FROM _recovered) AND filename LIKE 'hash_%'"
    )
    con.execute(
        "UPDATE assets SET resource = "
        "(SELECT resource FROM _recovered WHERE h = name_hash) "
        "WHERE name_hash IN (SELECT h FROM _recovered WHERE resource != '')"
    )


def backfill_resources(db_path: Any = None, game: str | None = None) -> int:
    # recover full internal paths for an already-scanned DA2 database without
    # deleting and rebuilding it; the next asset refresh shows the new column
    if str(game or "").upper() != "DA2":
        return 0
    db = _db_path(db_path, game)
    if not db.exists():
        return 0
    con = _connect(db)
    try:
        _ensure_columns(con)
        _recover_names(con, game, force=True)
        con.commit()
        row = con.execute(
            "SELECT COUNT(*) FROM assets WHERE resource IS NOT NULL AND resource != ''"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def payload(archive: Any, name: str, name_hash: Any = None) -> bytes:
    archive = Path(archive)
    # mmap: a full read of the biggest DA2 archive peaks at ~870MB, and the
    # reader only ever slices one entry out of it.
    with _mapped(archive) as data:
        reader = ERFReader(data)
        entry = next((e for e in reader.entries if name and e.name == name), None)
        wanted = None
        if entry is None and name_hash is not None:
            try:
                # bpy items carry it as 64-bit hex text; db rows as int
                wanted = _s64(int(name_hash, 16) if isinstance(name_hash, str) else int(name_hash))
            except ValueError:
                wanted = None
        elif entry is None and isinstance(name, str) and name.startswith("hash_"):
            try:
                wanted = _s64(int(name.split("_", 1)[1].split(".", 1)[0], 16))
            except ValueError:
                wanted = None
        if wanted is not None:
            # DA2 V3 entries are named by hash only; the db row carries it.
            entry = next(
                (e for e in reader.entries if _s64(getattr(e, "name_hash", 0)) == wanted),
                None,
            )
        if entry is None:
            raise KeyError(f"entry '{name}' not found in '{archive.name}'")
        return reader.decode_payload(data[entry.offset : entry.offset + entry.packed_size], entry)


def _where(ext: Any = None, name: Any = None, package: Any = None) -> tuple:
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


def count(
    ext: Any = None,
    name: Any = None,
    package: Any = None,
    db_path: Any = None,
    game: str | None = None,
) -> int:
    db = _db_path(db_path, game)
    if not db.exists():
        return 0
    where, params = _where(_norm_ext(ext), name, package)
    con = _connect(db)
    try:
        row = con.execute(f"SELECT COUNT(*) FROM assets{where}", params).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def find(
    ext: Any = None,
    name: Any = None,
    package: Any = None,
    db_path: Any = None,
    limit: int | None = None,
    offset: int = 0,
    game: str | None = None,
) -> list:
    db = _db_path(db_path, game)
    if not db.exists():
        return []
    if limit is not None:
        limit = int(limit)
        offset = int(offset)
        if limit < 0 or offset < 0:
            raise ValueError("limit/offset must be >= 0")
    where, params = _where(_norm_ext(ext), name, package)

    # rowid order = scan insertion order, so pages are stable
    sql = f"SELECT package, filename, ext, path, name_hash, resource FROM assets{where} ORDER BY rowid"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]

    con = _connect(db)
    con.row_factory = sqlite3.Row
    try:
        _ensure_columns(con)
        out = [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
    return out


def find_exact(name: str, db_path: Any = None, game: str | None = None) -> dict | None:
    # loose files first so we skip extracting, then the latest-loaded package
    # so a player's override beats the stock data it shadows
    if not name:
        return None
    db = _db_path(db_path, game)
    if not db.exists():
        return None

    con = _connect(db)
    con.row_factory = sqlite3.Row
    try:
        _ensure_columns(con)
        row = con.execute(
            "SELECT package, filename, ext, path, name_hash, resource FROM assets "
            "WHERE ext = ? AND filename = ? COLLATE NOCASE "
            "ORDER BY package IS NOT NULL, prio DESC, package, path LIMIT 1",
            (_norm_ext(Path(name).suffix), name),
        ).fetchone()
        if row is None:
            # DA2 indexes assets by the FNV-1 hash of the lowercased path, so a
            # label such as 'art\\vfx\\models\\vfx_shield_uhm.msh' resolves by
            # hash even though the entry is stored under a synthetic name.
            # DAO rows all carry hash 0, so this never matches them
            wanted = _s64(fnv1(str(name).lower()))
            row = con.execute(
                "SELECT package, filename, ext, path, name_hash, resource FROM assets "
                "WHERE name_hash = ? "
                "ORDER BY package IS NOT NULL, prio DESC, package, path LIMIT 1",
                (wanted,),
            ).fetchone()
    finally:
        con.close()

    if row is not None:
        return dict(row)
    return None


def load(db_path: Any = None) -> list:
    return find(db_path=db_path)
