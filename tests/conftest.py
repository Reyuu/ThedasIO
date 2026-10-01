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
