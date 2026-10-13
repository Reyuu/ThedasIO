"""Shared fixtures: sample files, small builders, and a clean import registry.

The registry is module-level state in thedas_io.export, so every
test that touches it must leave it as it found it. `clean_registry` does that
autouse; `only_item` additionally empties it for tests that need a known set.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

import fake_bpy

sys.modules.setdefault("bpy", fake_bpy.bpy)
sys.modules.setdefault("mathutils", fake_bpy.mathutils)

from thedas_io.export import ITEMS, reset

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def fourcc(text: str) -> int:
    # 'mshh' -> the little-endian u32 a GFF struct-def carries.
    return int.from_bytes(text.encode("ascii"), "little")


def fourcc_bytes(value: int) -> bytes:
    # The inverse of fourcc(), for asserting on a parsed struct-def.
    return value.to_bytes(4, "little")


def load(name: str):
    # (raw bytes, GFFReader) for a sample in data/.
    from thedas_io.gff40.description import GFFReader

    raw = (DATA_DIR / name).read_bytes()
    return raw, GFFReader(raw)


def archive(path, items) -> Path:
    # Write an ERF holding `items` (name -> bytes) and return its path.
    from thedas_io.erf.description import ERFWriter

    writer = ERFWriter()
    for name, payload in items.items():
        writer.add_entry(name, payload)
    path.write_bytes(writer.write())
    return path


def encrypted_archive(path, items, password: str, module_id: int = 0) -> Path:
    # Write an ERF V2.2 holding `items`, Blowfish-encrypted the way a DAO DLC
    # module does. `password` must be a stock module password so that the
    # reader can resolve it from the header digest.
    import hashlib

    from Crypto.Cipher import Blowfish

    from thedas_io.erf.description import MAGIC_22

    cipher = Blowfish.new(struct.pack("<Q", int(password)), Blowfish.MODE_ECB)
    rows = []
    payload_data = b""
    offset = 56 + len(items) * 76
    for name, data in items.items():
        # pad up to the block with the pad count, as the engine does, so
        # packed_size exceeds size by 1..7 bytes
        pad = -len(data) % 8
        packed_size = len(data) + pad
        rows.append((name, offset, packed_size, len(data)))
        payload_data += cipher.encrypt(data + bytes([pad]) * pad) if pad else cipher.encrypt(data)
        offset += packed_size

    header = MAGIC_22 + struct.pack(
        "<6I16s",
        len(items),
        0,
        0,
        0xFFFFFFFF,
        2 << 4,  # encryption id 2, bits 4..7 of the flags word
        module_id,
        hashlib.md5(password.encode("ascii")).digest(),
    )
    toc = b"".join(
        name.encode("utf-16le").ljust(64, b"\x00") + struct.pack("<III", off, plen, size)
        for name, off, plen, size in rows
    )
    path.write_bytes(header + toc + payload_data)
    return path


def zlib_archive(path, items) -> Path:
    # Write an ERF V2.1 holding `items`: ascii names, zlib payloads, and the
    # 44-byte rows dao_prc_gib's env areas use.
    import zlib

    from thedas_io.erf.description import MAGIC_21

    rows = []
    payload_data = b""
    offset = 32 + len(items) * 44
    for name, data in items.items():
        blob = zlib.compress(data)
        rows.append((name, offset, len(blob), len(data)))
        payload_data += blob
        offset += len(blob)

    header = MAGIC_21 + struct.pack("<4I", len(items), 0, 0x004F006E, 0x0000FFFF)
    toc = b"".join(
        name.encode("ascii").ljust(32, b"\x00") + struct.pack("<3I", off, plen, size)
        for name, off, plen, size in rows
    )
    path.write_bytes(header + toc + payload_data)
    return path


def struct_defs_blob(reader) -> tuple[bytes, bytes]:
    # (struct-defs, fields) of a parsed GFF, for asserting on a re-encode.
    defs = b"".join(
        struct.pack(
            "<IIII", sd.type_fourcc, len(sd.field_instances), sd.field_offset, sd.struct_size
        )
        for sd in reader.struct_defs
    )
    fields = b"".join(f.pack() for sd in reader.struct_defs for f in sd.field_instances)
    return defs, fields


@pytest.fixture(autouse=True)
def clean_registry():
    # Keep the export registry from leaking between tests.
    yield
    reset()


@pytest.fixture
def only_item():
    # An empty registry, restored afterwards.
    reset()
    yield ITEMS
    reset()
