from __future__ import annotations

import struct

from conftest import archive
from thedas_io.asset_db.scanner import find, load, scan
from thedas_io.erf.description import ERFReader, ERFWriter


def test_scan_and_load(tmp_path):
    (tmp_path / "sub").mkdir()
    archive(tmp_path / "p1.erf", {"a.msh": b"data"})
    archive(tmp_path / "sub" / "p2.rim", {"b.mmh": b"xy", "c.txt": b"z"})
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db) == {"files": 2, "entries": 3, "skipped": []}
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


def test_real_magic_layout():
    w = ERFWriter()
    w.add_entry("a.txt", b"abc")
    raw = w.write()
    assert raw[:16] == "ERF V2.0".encode("utf-16-le")
    assert ERFReader(raw).get_payload("a.txt") == b"abc"
    assert struct.unpack("<IIII", raw[16:32])[3] == 0xFFFFFFFF
