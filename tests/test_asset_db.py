from __future__ import annotations

import sqlite3
import struct
from typing import ClassVar

import pytest

from conftest import archive, encrypted_archive, zlib_archive
from thedas_io.asset_db.scanner import _s64, find, find_exact, fnv1, load, payload, scan
from thedas_io.assets import extract
from thedas_io.erf.description import MAGIC, MAGIC_V3, ERFReader, ERFWriter


def _v3_archive(path, entries):
    # DA2 layout: entries carry only a name hash, never a name string.
    rows = b""
    blob = b""
    offset = 48 + 28 * len(entries)
    for name_hash, type_hash, data in entries:
        rows += struct.pack(
            "<iQIIII", -1, name_hash, type_hash, offset + len(blob), len(data), len(data)
        )
        blob += data
    path.write_bytes(
        MAGIC_V3 + struct.pack("<IIII", 0, len(entries), 0, 0) + b"\x00" * 16 + rows + blob
    )


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


def test_da2_recovers_hash_only_names(tmp_path, monkeypatch):
    # DA2 stores no names. MMH label 6005 holds the mesh path, and mmh/phy/mao
    # siblings share that stem, so the scan has to hash those candidates and
    # rename the matching rows (and fix a '.bin' ext it could not sniff).
    stem = "art\\models\\thing"
    gff_mmh = b"GFF V4.0PC  MMH " + b"\x00" * 8

    class _Tree:
        values: ClassVar = {6005: stem + ".msh"}

    monkeypatch.setattr(
        "thedas_io.mmh.description.MMHReader",
        lambda data: type("_R", (), {"tree": _Tree()})(),
    )
    _v3_archive(
        tmp_path / "a.erf",
        [
            (fnv1(stem + ".mmh"), 0x427E18E9, gff_mmh),
            (fnv1(stem + ".msh"), 0x2C7DF5B7, b"MESHPAYLOAD"),
            (fnv1(stem + ".phy"), 0x43775E36, b"PHYPAYLOAD12345"),
            (fnv1(stem + ".mao"), 0x3E7E125A, b'<?xml version="1.0"?><MaterialOverride/>'),
        ],
    )
    db = tmp_path / "t.sqlite"
    assert scan(tmp_path, db, game="DA2")["entries"] == 4
    assert {(r["filename"], r["ext"]) for r in load(db)} == {
        ("thing.mmh", ".mmh"),
        ("thing.msh", ".msh"),
        ("thing.phy", ".phy"),
        ("thing.mao", ".mao"),
    }
    # a V3 entry has no name of its own, so extracting a recovered row has to
    # resolve it through the stored hash instead of the display name
    row = find_exact("thing.mmh", db_path=db, game="DA2")
    assert row is not None and row["name_hash"] == _s64(fnv1(stem + ".mmh"))
    # a label such as a texture resref resolves by hash even when the caller
    # (material apply) does not know which game is loaded
    assert find_exact(stem + ".mmh", db_path=db) is not None
    assert extract(row, dest_dir=tmp_path / "out") == tmp_path / "out" / "thing.mmh"
    assert (tmp_path / "out" / "thing.mmh").read_bytes() == gff_mmh
    # the UI can only keep the 64-bit hash as hex text, not in a bpy int
    assert (
        payload(row["path"], row["filename"], f'{row["name_hash"] & 0xFFFFFFFFFFFFFFFF:016x}')
        == gff_mmh
    )


def test_da2_extension_comes_from_the_entry_type_hash(tmp_path):
    # ERF V3 stores fnv32(ext), so a hash-only entry gets its real extension
    # even when the payload cannot be sniffed (and without decompressing it).
    cases = [
        ("art\\x\\y.msh", 0x2C7DF5B7, ".msh"),
        ("art\\x\\z.ogg", 0x38798BF2, ".ogg"),
        ("art\\x\\w.unknown", 0xDEADBEEF, ".bin"),
    ]
    _v3_archive(
        tmp_path / "a.erf",
        [(fnv1(name), type_hash, b"\x00\x01\x02\x03") for name, type_hash, _ in cases],
    )
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db, game="DA2")
    assert {(r["filename"], r["ext"]) for r in load(db)} == {
        (f"hash_{fnv1(name):016x}{ext}", ext) for name, _, ext in cases
    }


def test_da2_bundled_hash_names_rename_and_fix_the_extension(tmp_path, monkeypatch):
    # names recovered offline are keyed by the exact FNV-1 hash, so they win
    # over the payload sniff, which mislabels e.g. .mor as plain .gff
    from thedas_io.asset_db import da2_data

    stem = "art\\models\\recovered"
    monkeypatch.setattr(da2_data, "names", lambda: {_s64(fnv1(stem + ".mor")): "recovered.mor"})
    _v3_archive(
        tmp_path / "a.erf",
        [(fnv1(stem + ".mor"), 0x00000000, b"GFF V4.0PC  ZZZZ" + b"\x00" * 8)],
    )
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db, game="DA2")
    rows = [(r["filename"], r["ext"]) for r in load(db)]
    assert rows == [("recovered.mor", ".mor")]


def test_da2_wordlist_hashes_are_precomputed_and_released(tmp_path):
    # the bundled wordlist is stored already hashed, so a hash-only entry is
    # named without the scanner running FNV-1 over 279k paths; the table is
    # dropped again once the scan finishes
    from thedas_io.asset_db import da2_data

    name = "art\\vfx\\models\\vfx_shield_uhm.msh"
    _v3_archive(tmp_path / "a.erf", [(fnv1(name), 0, b"not a payload")])
    scan(tmp_path, tmp_path / "t.sqlite", game="DA2")
    assert [(r["filename"], r["ext"]) for r in load(tmp_path / "t.sqlite")] == [
        ("vfx_shield_uhm.msh", ".msh")
    ]
    assert da2_data._CACHE == {}


def test_failed_scan_closes_the_connection_and_keeps_the_old_index(tmp_path, monkeypatch):
    # A scan clears the table first, so a failure part way through must roll
    # that DELETE back and release the connection; otherwise the user is left
    # with an empty list and a locked database.
    from thedas_io.asset_db import scanner

    archive(tmp_path / "ok.erf", {"a.txt": b"abc"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    assert [r["filename"] for r in load(db)] == ["a.txt"]

    state = {}

    class Tracked:
        def __init__(self, con):
            self.con = con
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.con, name)

        def __setattr__(self, name, value):
            if name in ("con", "closed"):
                object.__setattr__(self, name, value)
            else:
                setattr(self.con, name, value)

        def close(self):
            self.closed = True
            self.con.close()

    real_connect = scanner.sqlite3.connect

    def connect(*args, **kwargs):
        state["con"] = Tracked(real_connect(*args, **kwargs))
        return state["con"]

    def boom(path, prio):
        raise RuntimeError("boom")

    monkeypatch.setattr(scanner.sqlite3, "connect", connect)
    monkeypatch.setattr(scanner, "_archive_rows", boom)

    with pytest.raises(RuntimeError, match="boom"):
        scanner.scan(tmp_path, db)

    tracked = state["con"]
    assert tracked.closed is True
    assert [r["filename"] for r in load(db)] == ["a.txt"]


def test_scan_uses_wal_and_a_busy_timeout(tmp_path):
    from thedas_io.asset_db import scanner

    archive(tmp_path / "a.erf", {"a.txt": b"x"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    con = scanner._connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        con.close()


def test_find_reads_while_a_write_holds_the_database(tmp_path):
    # WAL: an EXCLUSIVE writer blocks other writers but never a reader. With
    # the default journal this query would wait out the timeout and then raise
    # "database is locked", which is what a search during a scan used to show.
    archive(tmp_path / "a.erf", {"a.txt": b"x"})
    db = tmp_path / "t.sqlite"
    scan(tmp_path, db)
    writer = sqlite3.connect(db)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO assets VALUES (NULL, 'late.txt', '.txt', 'x', 0, 0)")
    try:
        assert [r["filename"] for r in find(db_path=db)] == ["a.txt"]
    finally:
        writer.rollback()
        writer.close()
