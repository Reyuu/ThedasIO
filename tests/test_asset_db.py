from __future__ import annotations

import sqlite3
import struct

import pytest

from conftest import archive, encrypted_archive, zlib_archive
from thedas_io.asset_db.scanner import find, find_exact, load, payload, scan
from thedas_io.erf.description import MAGIC, ERFReader, ERFWriter


def test_scan_and_load(tmp_path):
    (tmp_path / "sub").mkdir()
    archive(tmp_path / "p1.erf", {"a.msh": b"data"})
    archive(tmp_path / "sub" / "p2.rim", {"b.mmh": b"xy", "c.txt": b"z"})
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db) == {"files": 2, "entries": 3, "skipped": [], "missing": []}
    rows = {(r["package"], r["filename"], r["ext"]) for r in load(db)}
    assert rows == {
        ("p1.erf", "a.msh", ".msh"),
        ("p2.rim", "b.mmh", ".mmh"),
        ("p2.rim", "c.txt", ".txt"),
    }
    assert all(r["path"].startswith(str(tmp_path)) for r in load(db))
    scan(tmp_path, db)
    assert len(load(db)) == 3  # rescan idempotent


def test_find(tmp_path):
    (tmp_path / "sub").mkdir()
    archive(tmp_path / "p1.erf", {"a.msh": b"data", "b.msh": b"xy"})
    archive(tmp_path / "sub" / "p2.rim", {"a.msh": b"z"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    assert {(r["package"], r["filename"]) for r in find(ext=".msh", db_path=db)} == {
        ("p1.erf", "a.msh"),
        ("p1.erf", "b.msh"),
        ("p2.rim", "a.msh"),
    }
    assert [r["filename"] for r in find(ext=".MSH", db_path=db)] != []
    assert {(r["package"], r["filename"]) for r in find(ext="msh", db_path=db)} == {
        ("p1.erf", "a.msh"),
        ("p1.erf", "b.msh"),
        ("p2.rim", "a.msh"),
    }
    assert [(r["package"], r["filename"]) for r in find(name="a.m", db_path=db)] == [
        ("p1.erf", "a.msh"),
        ("p2.rim", "a.msh"),
    ]
    assert [r["filename"] for r in find(package="p2.rim", db_path=db)] == ["a.msh"]
    assert find(ext=".dds", db_path=db) == []
    assert find(db_path=tmp_path / "nope.sqlite") == []


def test_load_missing(tmp_path):
    assert load(tmp_path / "nope.sqlite") == []


def test_corrupt_skipped(tmp_path):
    (tmp_path / "b.rim").write_bytes(b"JUNK" + b"\x00" * 100)
    archive(tmp_path / "ok.erf", {"a.txt": b"abc"})
    assert scan(tmp_path, tmp_path / "t.sqlite")["skipped"] == [str(tmp_path / "b.rim")]
    assert len(load(tmp_path / "t.sqlite")) == 1  # corrupt archive yields no entries


def test_entry_count_beyond_the_file_is_skipped_not_allocated(tmp_path):
    # a truncated or foreign archive reports a garbage count. read(count*72)
    # would then try to allocate up to 309GB, so the bound has to come from the
    # file size rather than a guessed ceiling.
    bad = tmp_path / "bad.rim"
    bad.write_bytes(MAGIC + struct.pack("<IIII", 0xFFFFFFFF, 0, 0, 0xFFFFFFFF))
    archive(tmp_path / "ok.erf", {"a.txt": b"abc"})

    stats = scan(tmp_path, tmp_path / "t.sqlite")
    assert stats["skipped"] == [str(bad)]
    assert [r["filename"] for r in load(tmp_path / "t.sqlite")] == ["a.txt"]


def test_a_large_but_real_entry_count_is_accepted(tmp_path):
    # the guard must not reject a genuinely big archive: 5000 entries in a file
    # that really holds them, which a fixed ceiling of a few thousand would
    # wrongly drop. real archives here top out at 1324, but nothing guarantees it.
    entries = {f"f{i:05d}.txt": b"x" for i in range(5000)}
    p = tmp_path / "big.rim"
    archive(p, entries)
    assert scan(tmp_path, tmp_path / "t.sqlite")["skipped"] == []
    assert len(load(tmp_path / "t.sqlite")) == 5000


def test_real_magic_layout():
    w = ERFWriter()
    w.add_entry("a.txt", b"abc")
    raw = w.write()
    assert raw[:16] == "ERF V2.0".encode("utf-16-le")
    assert ERFReader(raw).get_payload("a.txt") == b"abc"
    assert struct.unpack("<IIII", raw[16:32])[3] == 0xFFFFFFFF


def test_scan_takes_several_roots(tmp_path):
    game = tmp_path / "game"
    addins = tmp_path / "AddIns"
    game.mkdir()
    (addins / "mod_dao_prc_cp_1").mkdir(parents=True)
    archive(game / "base.erf", {"a.msh": b"base"})
    archive(addins / "mod_dao_prc_cp_1" / "dlc.rim", {"b.msh": b"dlc"})

    db = tmp_path / "t.sqlite"
    assert scan([game, addins], db)["entries"] == 2
    assert {(r["package"], r["filename"]) for r in load(db)} == {
        ("base.erf", "a.msh"),
        ("dlc.rim", "b.msh"),
    }


def test_missing_root_is_reported_not_fatal(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    archive(root / "p.erf", {"a.msh": b"x"})
    gone = tmp_path / "AddIns"

    stats = scan([root, gone], tmp_path / "t.sqlite")
    assert stats["missing"] == [str(gone)]
    assert stats["files"] == 1


def test_later_root_wins_the_same_file_name(tmp_path):
    game = tmp_path / "game"
    addins = tmp_path / "AddIns"
    game.mkdir()
    addins.mkdir()
    archive(game / "base.erf", {"arm.msh": b"stock"})
    archive(addins / "dlc.rim", {"arm.msh": b"modded"})

    db = tmp_path / "t.sqlite"
    scan([game, addins], db)
    # the engine loads DLC after the base game, so the AddIns copy has to win
    assert find_exact("arm.msh", db)["package"] == "dlc.rim"

    scan([game, addins], db)  # order of the roots is the load order, not a tie
    assert find_exact("arm.msh", db)["package"] == "dlc.rim"


def test_override_folder_beats_its_own_root(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "override").mkdir()
    archive(tmp_path / "data" / "base.erf", {"arm.msh": b"stock"})
    archive(tmp_path / "override" / "fix.rim", {"arm.msh": b"fixed"})

    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    assert find_exact("arm.msh", db)["package"] == "fix.rim"


def test_scan_migrates_a_database_without_priority(tmp_path):
    archive(tmp_path / "p.erf", {"a.msh": b"x"})
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE assets(package, filename, ext, path)")
    con.commit()
    con.close()

    scan(tmp_path, db)
    assert find_exact("a.msh", db)["package"] == "p.erf"


def test_scan_indexes_encrypted_dlc_archives(tmp_path):
    # a DLC module is ERF V2.2: the scan has to index it, not skip it, and the
    # payload has to come back decrypted and without block padding
    encrypted_archive(
        tmp_path / "dlc.rim", {"a.mmh": b"GFF V4.0 mesh", "b.msh": b"xyz"}, "13422531"
    )
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db)["skipped"] == []
    assert {(r["package"], r["filename"]) for r in load(db)} == {
        ("dlc.rim", "a.mmh"),
        ("dlc.rim", "b.msh"),
    }
    assert payload(tmp_path / "dlc.rim", "a.mmh") == b"GFF V4.0 mesh"
    assert payload(tmp_path / "dlc.rim", "b.msh") == b"xyz"


def test_scan_indexes_zlib_v21_archives(tmp_path):
    zlib_archive(tmp_path / "env.rim", {"plt_a.plo": b"GFF V4.0PLTS" * 20})
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db)["skipped"] == []
    assert payload(tmp_path / "env.rim", "plt_a.plo") == b"GFF V4.0PLTS" * 20


def test_unknown_module_is_indexed_but_its_payload_fails(tmp_path):
    encrypted_archive(tmp_path / "mod.rim", {"a.msh": b"x"}, "99999999", module_id=7)
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db)["skipped"] == []
    assert [r["filename"] for r in load(db)] == ["a.msh"]
    with pytest.raises(ValueError, match="no known password"):
        payload(tmp_path / "mod.rim", "a.msh")
