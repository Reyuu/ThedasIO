# parse every .mor/.mop in an asset database and report the corpus shape
#
#   python tools/check_morph_corpus.py --game DAO
#   python tools/check_morph_corpus.py --game DA2 --limit 200 --json
#
# a bad file is collected, never fatal: the point is naming what the reader
# cannot handle and comparing part/tint/node counts across the game.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from thedas_io.asset_db.scanner import _db_path
from thedas_io.assets import extract
from thedas_io.morph.description import MORReader, read

COLUMNS = ("package", "filename", "ext", "path", "prio", "name_hash", "resource")


def check(db_path: Path, limit: int | None = None) -> dict:
    stats: dict = {
        "db": str(db_path),
        "files": 0,
        "failed": 0,
        "empty": 0,
        "mor": 0,
        "mop": 0,
        "no_parts": 0,
        "nameless_nodes": 0,
        "parts": 0,
        "tints": 0,
        "nodes": 0,
        "vertices": 0,
        "material": 0,
        "vectors": 0,
        "model": 0,
        "textures": 0,
        "versions": {},
        "errors": [],
    }
    conn = sqlite3.connect(str(db_path))
    sql = (
        "select package, filename, ext, path, prio, name_hash, resource from assets "
        "where lower(ext) in ('.mor', '.mop') order by filename"
    )
    if limit:
        sql += f" limit {int(limit)}"
    rows = conn.execute(sql).fetchall()
    conn.close()

    for row in rows:
        row = dict(zip(COLUMNS, row))
        stats["files"] += 1
        raw = extract(row).read_bytes()
        if not raw:
            # the game ships a few zero-length placeholders; nothing to read
            stats["empty"] += 1
            continue
        try:
            preset = read(raw)
        except Exception as exc:  # noqa: BLE001 - the tool keeps going
            stats["failed"] += 1
            stats["errors"].append(
                {"file": row["filename"], "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        summary = preset.summary()
        version = preset.version.decode("ascii", "replace") if preset.version else "?"
        stats["versions"][version] = stats["versions"].get(version, 0) + 1
        stats["mor" if isinstance(preset, MORReader) else "mop"] += 1
        for key in (
            "parts",
            "tints",
            "nodes",
            "vertices",
            "material",
            "vectors",
            "model",
            "textures",
        ):
            stats[key] += summary.get(key, 0)
        if not any(part.name for part in preset.parts):
            stats["no_parts"] += 1
        stats["nameless_nodes"] += sum(1 for node in getattr(preset, "nodes", ()) if not node.name)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", default="DAO", choices=("DAO", "DA2"))
    ap.add_argument("--db", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else _db_path(None, args.game)
    stats = check(db, args.limit or None)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        for key, value in stats.items():
            if key != "errors":
                print(f"{key:14} {value}")
        for err in stats["errors"][:50]:
            print(f"  {err['file']}: {err['error']}")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
