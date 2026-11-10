"""The ANI corpus tool: point it at a database and it reports the fixtures."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from conftest import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from check_ani_corpus import check


def test_corpus_tool_reads_the_fixtures(tmp_path):
    db = tmp_path / "assets.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table assets(package, filename, ext, path, prio, name_hash, resource)")
    for name in ("c_dog.sit_p.ani", "prp_door.dying.ani"):
        conn.execute(
            "insert into assets values (?, ?, ?, ?, ?, ?, ?)",
            (None, name, ".ani", str(DATA_DIR / name), 0, 0, name),
        )
    conn.commit()
    conn.close()

    stats = check(db)
    assert stats["files"] == 2
    assert stats["failed"] == 0
    assert stats["nodes"] > 0
    assert stats["rotation32"] > 0
    assert stats["rotation64"] > 0
    assert stats["position"] > 0
    assert stats["versions"] == {"V0.1": 2}
