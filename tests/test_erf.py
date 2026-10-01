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

import pytest

from thedas_io.erf.description import ERFReader, ERFWriter


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
