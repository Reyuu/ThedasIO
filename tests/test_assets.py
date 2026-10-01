from __future__ import annotations

import pytest

from conftest import archive
from thedas_io.asset_db.scanner import payload
from thedas_io.assets import extract, importer_for


def test_importer_for():
    assert importer_for(".msh") == "msh"
    assert importer_for("MMH") == "mmh"
    assert importer_for("msh") == "msh"
    assert importer_for(".dds") is None
    assert importer_for(None) is None


def test_extract_loose(tmp_path):
    f = tmp_path / "a.msh"
    f.write_bytes(b"mesh")
    assert extract({"package": None, "filename": "a.msh", "ext": ".msh", "path": str(f)}) == f


def test_extract_archived(tmp_path):
    archive(tmp_path / "p.erf", {"a.msh": b"mesh-bytes"})
    row = {"package": "p.erf", "filename": "a.msh", "ext": ".msh", "path": str(tmp_path / "p.erf")}
    out = extract(row, tmp_path / "out")
    assert out.read_bytes() == b"mesh-bytes"
    assert payload(tmp_path / "p.erf", "a.msh") == b"mesh-bytes"


def test_extract_keeps_bare_name(tmp_path):
    archive(tmp_path / "p.erf", {"a.msh": b"from-p"})
    archive(tmp_path / "q.erf", {"a.msh": b"from-q"})
    rows = [
        {"package": p, "filename": "a.msh", "ext": ".msh", "path": str(tmp_path / f"{p}")}
        for p in ("p.erf", "q.erf")
    ]
    outs = [extract(r) for r in rows]
    assert [o.name for o in outs] == ["a.msh", "a.msh"]
    assert [o.read_bytes() for o in outs] == [b"from-p", b"from-q"]
    assert outs[0].parent != outs[1].parent  # unique dirs, no collision


def test_resolve_case_insensitive(tmp_path):
    from thedas_io.asset_db.scanner import scan
    from thedas_io.assets import resolve

    (tmp_path / "x_0dl3.dds").write_bytes(b"dds")
    archive(tmp_path / "p.erf", {"y_0dl3.dds": b"dds-archived"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    loose_row = {
        "package": None,
        "filename": "m.msh",
        "ext": ".msh",
        "path": str(tmp_path / "m.msh"),
    }
    hit = resolve("X_0DL3.DDS", loose_row, str(db))
    assert hit is not None and hit.read_bytes() == b"dds"
    arch_row = {
        "package": "p.erf",
        "filename": "m.msh",
        "ext": ".msh",
        "path": str(tmp_path / "p.erf"),
    }
    hit2 = resolve("Y_0DL3.DDS", arch_row, str(db))
    assert hit2 is not None and hit2.read_bytes() == b"dds-archived"


def test_resolve_local_only_never_touches_the_game(tmp_path):
    # Disk imports with local resolve: siblings or nothing, never the DB.
    from thedas_io.asset_db.scanner import scan
    from thedas_io.assets import resolve

    (tmp_path / "x_0d.dds").write_bytes(b"local")
    (tmp_path / "game_only.dds").write_bytes(b"from-db")
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    (tmp_path / "game_only.dds").unlink()  # only the DB knows it now
    row = {"package": None, "filename": "m.msh", "ext": ".msh", "path": str(tmp_path / "m.msh")}
    assert resolve("x_0d.dds", row, str(db), local_only=True).read_bytes() == b"local"
    assert resolve("game_only.dds", row, str(db), local_only=True) is None
    assert resolve("missing.dds", row, str(db), local_only=True) is None


def test_extract_missing_entry(tmp_path):
    archive(tmp_path / "p.erf", {"a.msh": b"x"})
    with pytest.raises(KeyError, match="not found"):
        extract(
            {
                "package": "p.erf",
                "filename": "b.msh",
                "ext": ".msh",
                "path": str(tmp_path / "p.erf"),
            },
            tmp_path,
        )
