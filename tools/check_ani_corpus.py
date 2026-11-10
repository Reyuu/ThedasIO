# Parse every .ani in an asset database and report the shape of the corpus.
#
#   python tools/check_ani_corpus.py --game DAO
#   python tools/check_ani_corpus.py --game DA2 --limit 500 --json
#
# A per-file failure is collected, never fatal: the point is to name the files
# the reader cannot handle and to compare track/keyframe counts across the game.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from thedas_io.ani.description import TRANSLATION, ANIReader, rotation_bits
from thedas_io.asset_db.scanner import _db_path
from thedas_io.assets import extract

COLUMNS = ("package", "filename", "ext", "path", "prio", "name_hash", "resource")


def check(db_path: Path, limit: int | None = None) -> dict:
    stats: dict = {
        "db": str(db_path),
        "files": 0,
        "failed": 0,
        "empty": 0,
        "nodes": 0,
        "keys": 0,
        "rotation32": 0,
        "rotation48": 0,
        "rotation64": 0,
        "position": 0,
        "scalar": 0,
        "additive": 0,
        "no_length": 0,
        "unsorted": 0,
        "skipped": 0,
        "warnings": 0,
        "versions": {},
        "targets": {},
        "errors": [],
    }
    conn = sqlite3.connect(str(db_path))
    sql = (
        "select package, filename, ext, path, prio, name_hash, resource from assets "
        "where lower(ext)='.ani' order by filename"
    )
    if limit:
        sql += f" limit {int(limit)}"
    rows = conn.execute(sql).fetchall()
    conn.close()

    for row in rows:
        row = dict(zip(COLUMNS, row))
        stats["files"] += 1
        try:
            reader = ANIReader(extract(row).read_bytes())
        except Exception as exc:  # noqa: BLE001 - the tool keeps going
            stats["failed"] += 1
            stats["errors"].append(
                {"file": row["filename"], "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        anim = reader.animation
        version = reader.version.decode("ascii", "replace") if reader.version else "?"
        stats["versions"][version] = stats["versions"].get(version, 0) + 1
        if not anim.nodes:
            stats["empty"] += 1
        if anim.length <= 0:
            stats["no_length"] += 1
        if anim.additive:
            stats["additive"] += 1
        stats["skipped"] += len(anim.skipped)
        stats["warnings"] += len(anim.warnings)
        for name, reason in anim.skipped[:5]:
            if len(stats["errors"]) < 50:
                stats["errors"].append(
                    {"file": row["filename"], "error": f"skipped {name}: {reason}"}
                )
        for node in anim.nodes:
            stats["targets"][str(node.target)] = stats["targets"].get(str(node.target), 0) + 1
            stats["nodes"] += 1
            stats["keys"] += len(node.keys)
            bits = rotation_bits(node.target)
            if bits == 32:
                stats["rotation32"] += 1
            elif bits == 48:
                stats["rotation48"] += 1
            elif bits == 64:
                stats["rotation64"] += 1
            elif node.target == TRANSLATION:
                stats["position"] += 1
            else:
                stats["scalar"] += 1
            times = [t for t, _ in node.keys]
            if any(b < a for a, b in pairwise(times)):
                stats["unsorted"] += 1
                if len(stats["errors"]) < 50:
                    stats["errors"].append(
                        {"file": row["filename"], "error": f"{node.name}: key times decrease"}
                    )
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
                print(f"{key:12} {value}")
        for err in stats["errors"][:50]:
            print(f"  {err['file']}: {err['error']}")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
