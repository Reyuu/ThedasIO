"""Tests for the ERF file reader and writer implementation.

The tests exercise the :class:`~thedas_io.erf.description.ERFReader`
and :class:`~thedas_io.erf.description.ERFWriter` classes.
They cover:

* round‑trip write → read consistency
* payload access and error handling
* validation of constraints (name length, magic string, sentinel)

The tests use ``pytest`` and the built‑in ``tmp_path`` fixture to avoid
touching any real files.
"""

from __future__ import annotations

import struct

import pytest

from conftest import encrypted_archive, zlib_archive
from thedas_io.erf.description import (
    MAGIC,
    MAGIC_21,
    MAGIC_22,
    ERFReader,
    ERFWriter,
    geometry,
)


def create_sample_erf() -> bytes:
    """Return a minimal but valid ERF file containing two entries.

    The layout follows the specification documented in
    :mod:`thedas_io.erf.description`.
    """

    writer = ERFWriter()
    writer.add_entry("file1.txt", b"hello world")
    writer.add_entry("file2.bin", b"\x01\x02\x03\x04")
    return writer.write()


def test_erf_roundtrip(tmp_path):
    # Write an ERF file and read it back, verifying data integrity.

    erf_bytes = create_sample_erf()
    file_path = tmp_path / "sample.erf"
    file_path.write_bytes(erf_bytes)

    reader = ERFReader(file_path.read_bytes())
    # Expect two entries
    assert len(reader.entries) == 2
    names = {e.name for e in reader.entries}
    assert names == {"file1.txt", "file2.bin"}
    # Payloads
    assert reader.get_payload("file1.txt") == b"hello world"
    assert reader.get_payload("file2.bin") == b"\x01\x02\x03\x04"

    # Round‑trip: write back and compare bytes
    writer = ERFWriter()
    for entry in reader.entries:
        writer.add_entry(entry.name, reader.get_payload(entry.name))
    roundtrip_bytes = writer.write()
    assert roundtrip_bytes == erf_bytes


def test_get_payload_missing_entry():
    # Requesting a non‑existent entry should raise :class:`KeyError`.

    erf_bytes = create_sample_erf()
    reader = ERFReader(erf_bytes)
    with pytest.raises(KeyError, match="Entry 'missing.txt' not found"):
        reader.get_payload("missing.txt")


def test_name_length_limit():
    # Names longer than 31 characters are rejected when writing.

    writer = ERFWriter()
    long_name = "a" * 32  # 32 characters, exceeds limit
    with pytest.raises(ValueError, match="Entry name must be at most 31 characters"):
        writer.add_entry(long_name, b"data")


def test_invalid_magic(tmp_path):
    # ERF files with an incorrect magic string should raise :class:`ValueError`.

    erf_bytes = create_sample_erf()
    # Corrupt the magic by changing the first byte
    corrupted = bytearray(erf_bytes)
    corrupted[0] = 0xFF
    with pytest.raises(ValueError, match="Invalid ERF file magic"):
        ERFReader(bytes(corrupted))


def test_invalid_sentinel(tmp_path):
    # ERF files with an incorrect sentinel should raise :class:`ValueError`.

    erf_bytes = create_sample_erf()
    corrupted = bytearray(erf_bytes)
    # The sentinel is at offset 28-32 (four bytes). Flip the last byte.
    corrupted[31] ^= 0xFF
    with pytest.raises(ValueError, match="Invalid ERF file sentinel"):
        ERFReader(bytes(corrupted))


# --- encrypted ERF V2.2 (the DLC modules) and zlib ERF V2.1 (env areas) ---

# a stock module password, so the reader can resolve it from the header digest
PASSWORD = "13422531"


def test_v22_roundtrip(tmp_path):
    # A V2.2 archive's names are readable but its payloads are not: decrypting
    # has to strip the block padding, or the caller gets trailing garbage.
    items = {"a.mmh": b"GFF V4.0" + b"\x00" * 100, "b.msh": b"x" * 7, "c.txt": b"y" * 8}
    p = encrypted_archive(tmp_path / "p.erf", items, PASSWORD, module_id=145)

    reader = ERFReader(p.read_bytes())
    assert [e.name for e in reader.entries] == list(items)
    assert reader.encryption == 2
    assert reader.module_id == 145
    for name, data in items.items():
        assert reader.get_payload(name) == data
        entry = reader.entry(name)
        # padding only appears when the plaintext is not block aligned
        assert entry.size == len(data)
        assert entry.packed_size - entry.size == -len(data) % 8


def test_v22_table_of_contents_needs_no_password(tmp_path):
    # Names are not encrypted, so an archive from a module we have no password
    # for still has to reach the database -- only the extraction may fail.
    p = encrypted_archive(tmp_path / "p.erf", {"a.mmh": b"data"}, "99999999", module_id=999)

    reader = ERFReader(p.read_bytes())
    assert [e.name for e in reader.entries] == ["a.mmh"]
    with pytest.raises(ValueError, match="no known password for ERF module 999"):
        reader.get_payload("a.mmh")


def test_v22_rejects_a_flag_we_cannot_honour(tmp_path):
    # Unsupported encryption or compression has to say so rather than hand
    # back ciphertext that looks like a corrupt payload.
    p = encrypted_archive(tmp_path / "p.erf", {"a.mmh": b"data"}, PASSWORD)
    raw = bytearray(p.read_bytes())
    flags = struct.unpack("<I", raw[32:36])[0]

    for bits, expected in (((4) << 4, "encryption"), ((2) << 29, "compression")):
        broken = bytearray(raw)
        struct.pack_into("<I", broken, 32, flags | bits)
        with pytest.raises(ValueError, match=f"unsupported ERF {expected}"):
            ERFReader(bytes(broken)).get_payload("a.mmh")


def test_v21_roundtrip(tmp_path):
    # V2.1 has ascii names, 44-byte rows and zlib payloads. dao_prc_gib ships
    # these next to its env content.
    items = {"a.plo": b"GFF V4.0" + b"\x01" * 500, "b.plo": b"z" * 40}
    p = zlib_archive(tmp_path / "env.rim", items)

    reader = ERFReader(p.read_bytes())
    assert [e.name for e in reader.entries] == list(items)
    for name, data in items.items():
        assert reader.get_payload(name) == data
    assert [e.packed_size <= e.size for e in reader.entries] == [True, True]


def test_geometry_matches_the_three_real_layouts():
    # (header, row) is what the scanner sizes its read from; getting it wrong
    # either truncates the table or reads payload bytes as entries.
    assert geometry(MAGIC) == (32, 72)
    assert geometry(MAGIC_21) == (32, 44)
    assert geometry(MAGIC_22) == (56, 76)
    with pytest.raises(ValueError, match="Invalid ERF file magic"):
        geometry(b"NOT AN ERF\x00\x00\x00\x00\x00\x00\x00\x00\x00")


def test_name_with_a_broken_utf16_tail_still_lists_the_archive():
    # 31 characters fill 62 bytes; a lone high surrogate fills the last two.
    # Decoding must not raise, or the whole archive drops out of the database.
    name_field = ("n" * 31).encode("utf-16le") + b"\x00\xd8"
    raw = (
        MAGIC
        + struct.pack("<IIII", 1, 0, 0, 0xFFFFFFFF)
        + name_field
        + struct.pack("<II", 32 + 72, 4)
        + b"data"
    )

    reader = ERFReader(raw)
    assert [e.name for e in reader.entries] == ["n" * 31]
    assert reader.get_payload("n" * 31) == b"data"


def test_truncated_table_of_contents_is_rejected():
    # the header claims two rows but the file stops inside them; the reader
    # must say so instead of walking past the buffer
    raw = create_sample_erf()
    with pytest.raises(ValueError, match="table of contents"):
        ERFReader(raw[:40])


def test_entry_running_past_the_end_is_rejected():
    # V2.0 row: 64-byte name field, then offset and size. Point the first entry
    # beyond the file so the scan reports the archive instead of indexing it.
    raw = bytearray(create_sample_erf())
    struct.pack_into("<I", raw, 32 + 64, len(raw) + 8)
    with pytest.raises(ValueError, match="runs past end of file"):
        ERFReader(bytes(raw))


def test_a_missing_key_is_only_probed_once(tmp_path):
    # every unnamed entry asks for the key; without caching the failure, a
    # hash-only archive of a module we cannot open re-probes the whole
    # candidate list per entry
    p = encrypted_archive(tmp_path / "p.erf", {"a.mmh": b"data"}, "99999999", module_id=999)
    reader = ERFReader(p.read_bytes())

    calls = []
    probe = reader._find_key

    def counted():
        calls.append(1)
        return probe()

    reader._find_key = counted
    for _ in range(3):
        with pytest.raises(ValueError, match="no known password for ERF module 999"):
            reader.get_payload("a.mmh")
    assert len(calls) == 1


def test_the_matched_password_is_cached(tmp_path):
    p = encrypted_archive(tmp_path / "p.erf", {"a.mmh": b"data"}, PASSWORD, module_id=145)
    reader = ERFReader(p.read_bytes())
    assert reader.key == struct.pack("<Q", int(PASSWORD))
    assert reader._password == PASSWORD

    from thedas_io.erf import description

    original = description.DAO_PASSWORDS
    description.DAO_PASSWORDS = ()
    try:
        assert reader._matched_password() == PASSWORD
    finally:
        description.DAO_PASSWORDS = original


def test_da2_key_probes_are_cached_per_module(monkeypatch):
    from thedas_io.erf import description

    reader = ERFReader(create_sample_erf())
    reader.pw_hash = b"unit-test-module"
    calls = []
    monkeypatch.setattr(reader, "_probe_da2_key", lambda: calls.append(1) or b"\x01" * 16)
    assert reader._da2_key() == b"\x01" * 16
    assert reader._da2_key() == b"\x01" * 16
    assert len(calls) == 1

    # a different module digest probes again
    reader.pw_hash = b"unit-test-other"
    assert reader._da2_key() == b"\x01" * 16
    assert len(calls) == 2
    for digest in (b"unit-test-module", b"unit-test-other"):
        description._DA2_KEYS.pop(digest, None)
