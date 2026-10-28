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


def test_resolve_takes_the_leaf_of_a_da2_path(tmp_path):
    # DA2 puts a full internal path in mmh 6005; the sibling sits beside it as a leaf
    from thedas_io.assets import resolve

    (tmp_path / "y.msh").write_bytes(b"mesh")
    row = {"package": None, "filename": "x.mmh", "ext": ".mmh", "path": str(tmp_path / "x.mmh")}
    for ref in ("art/characters/y.msh", "art\\characters\\y.msh"):
        assert resolve(ref, row, local_only=True) == tmp_path / "y.msh"
        assert resolve(ref, row) == tmp_path / "y.msh"


def test_resolve_takes_the_leaf_from_the_db(tmp_path):
    from thedas_io.asset_db.scanner import scan
    from thedas_io.assets import resolve

    archive(tmp_path / "p.erf", {"y.msh": b"archived"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    row = {"package": None, "filename": "x.mmh", "ext": ".mmh", "path": str(tmp_path / "x.mmh")}
    hit = resolve("art\\characters\\y.msh", row, str(db))
    assert hit is not None and hit.read_bytes() == b"archived"
    # local-only still means local or nothing, even when the DB has the leaf
    assert resolve("art\\characters\\y.msh", row, str(db), local_only=True) is None


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


def test_extract_strips_archive_paths(tmp_path):
    # an entry name is archive-controlled: `..\..\evil.msh` must not escape
    name = "..\\..\\evil.msh"
    archive(tmp_path / "p.erf", {name: b"payload"})
    row = {
        "package": "p.erf",
        "filename": name,
        "ext": ".msh",
        "path": str(tmp_path / "p.erf"),
    }
    out = extract(row, tmp_path / "out")
    assert out == tmp_path / "out" / "evil.msh"
    assert out.read_bytes() == b"payload"
    assert not (tmp_path / "evil.msh").exists()


def test_extract_sanitises_the_package_dir(tmp_path):
    from thedas_io import assets

    archive(tmp_path / "p.erf", {"a.msh": b"payload"})
    row = {
        "package": "..\\escape.erf",
        "filename": "a.msh",
        "ext": ".msh",
        "path": str(tmp_path / "p.erf"),
    }
    try:
        out = extract(row)
        assert assets._extract_dir() in out.parents
        assert out.name == "a.msh" and out.parent.name == "escape.erf"
        assert out.read_bytes() == b"payload"
    finally:
        assets.cleanup_session()


@pytest.mark.parametrize("name", ["..", ".", "", "C:"])
def test_extract_rejects_unusable_entry_names(tmp_path, name):
    with pytest.raises(ValueError, match="no safe file name"):
        extract(
            {
                "package": "p.erf",
                "filename": name,
                "ext": ".msh",
                "path": str(tmp_path / "p.erf"),
            },
            tmp_path / "out",
        )


def test_cleanup_session_removes_the_scratch_dir():
    from thedas_io import assets

    directory = assets._extract_dir()
    assert directory.is_dir()
    assets.cleanup_session()
    assert not directory.exists()
    assert assets._SESSION_DIR is None
