# A ``mshs`` collision shape stores its geometry as an ``NXS`` blob: the literal
# ``b"NXS"``, a version byte, the ``b"MESH"`` section tag, then::
#
#     0                     4   "NXS" + version
#     4                     4   "MESH"
#     8                     4   1
#     12                    4   bits per axis of the accelerator's coordinates
#                                (8 = plain float record, 10 and 18 quantised)
#     16                    4   quantum, always 0.001 (millimetres)
#     20                    4   255, or 2 where indices are two bytes wide
#     24                    4   quantisation origin (float)
#     28                    4   vertex count
#     32                    4   triangle count
#     36           12 * A       vertex positions
#     ...       3 * B * W       triangle indices, W = 1 if A <= 256 else 2
#     end of indices         the collision accelerator (see ``accelerator``)
#
# Positions and indices are all this module interprets; the tail is carried
# through byte for byte, so a mesh can be rescaled or moved.
#
# Verified against every mesh shape in the DAO install: every stream parses and
# no index points past its vertex array.


from __future__ import annotations

import struct
from typing import Any

MAGIC = b"NXS"
SECTION = b"MESH"
VERSION = 1
HEADER_SIZE = 36

OFF_VERSION = 3
OFF_SECTION = 4
OFF_VERTS = 28
OFF_TRIS = 32

# index width flips at 256: a byte cannot address vertex 256
U8_LIMIT = 256


class NxsMesh:
    __slots__ = ("raw", "tris", "vertices")

    def __init__(self, raw: bytes, vertices: list, tris: list) -> None:
        self.raw = raw
        self.vertices = vertices
        self.tris = tris

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def tri_count(self) -> int:
        return len(self.tris)

    def bounds(self) -> tuple:
        if not self.vertices:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def translated(self, offset: Any) -> NxsMesh:
        dx, dy, dz = offset
        moved = [(x + dx, y + dy, z + dz) for x, y, z in self.vertices]
        return NxsMesh(_write(moved, self.tris, self.raw), moved, self.tris)

    def scaled(self, factor: float) -> NxsMesh:
        f = float(factor)
        scaled = [(x * f, y * f, z * f) for x, y, z in self.vertices]
        return NxsMesh(_write(scaled, self.tris, self.raw), scaled, self.tris)


def _index_width(verts: int) -> int:
    return 1 if verts <= U8_LIMIT else 2


def _write(vertices: list, tris: list, template: bytes) -> bytes:
    # Rebuild the stream from arrays, preserving template's header and tail.
    verts = len(vertices)
    tri_count = len(tris)
    width = _index_width(verts)
    start = HEADER_SIZE + 12 * verts
    idx_bytes = 3 * tri_count * width
    head = bytearray(template[:HEADER_SIZE])
    struct.pack_into("<II", head, OFF_VERTS, verts, tri_count)
    positions = struct.pack(f"<{verts * 3}f", *[c for v in vertices for c in v])
    indices = template[start : start + idx_bytes]
    return bytes(head) + positions + indices + template[start + idx_bytes :]


def is_nxs(raw: bytes) -> bool:
    return len(raw) > HEADER_SIZE and raw[:3] == MAGIC and raw[4:8] == SECTION


def parse(raw: bytes) -> NxsMesh:
    if not is_nxs(raw):
        raise ValueError("not an NXS MESH stream")
    if raw[OFF_VERSION] != VERSION:
        raise ValueError(f"unsupported NXS version {raw[OFF_VERSION]}")
    verts, tris = struct.unpack_from("<II", raw, OFF_VERTS)
    width = _index_width(verts)

    pos_bytes = 12 * verts
    idx_bytes = 3 * tris * width
    if HEADER_SIZE + pos_bytes + idx_bytes > len(raw):
        raise ValueError(
            f"NXS declares {verts} verts / {tris} tris "
            f"({pos_bytes + idx_bytes} B of arrays) but the stream is "
            f"{len(raw)} B"
        )

    flat = struct.unpack_from(f"<{verts * 3}f", raw, HEADER_SIZE)
    vertices = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]

    base = HEADER_SIZE + pos_bytes
    fmt = f"<{3 * tris}{'B' if width == 1 else 'H'}"
    idx = struct.unpack_from(fmt, raw, base)
    limit = verts
    for v in idx:
        if v >= limit:
            raise ValueError(f"NXS triangle references vertex {v} of {verts}")
    tri_list = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx), 3)]
    return NxsMesh(raw, vertices, tri_list)


def looks_like_mesh(raw: bytes) -> bool:
    if not is_nxs(raw):
        return False
    try:
        parse(raw)
    except (ValueError, struct.error):
        return False
    return True


# after the indices: the triangle tree's internal nodes, then OPC and HBM
SECTIONS = (b"OPC", b"HBM")


# map the accelerator after a mesh's indices: internal node count, then OPC and
# HBM. bits == 8 = plain floats, 10/18 = quantised. The +64 floats and OPC pairs
# are not decoded; the game's own cooker writes them.
def accelerator(raw: bytes) -> dict:
    verts, tris = struct.unpack_from("<II", raw, OFF_VERTS)
    start = HEADER_SIZE + 12 * verts + 3 * tris * _index_width(verts)
    tail = raw[start:]
    out: dict = {
        "start": start,
        "length": len(tail),
        "nodes": None,
        "bits": struct.unpack_from("<I", raw, 12)[0],
        "quantum": struct.unpack_from("<f", raw, 16)[0],
        "origin": struct.unpack_from("<f", raw, 24)[0],
        "sections": {},
    }
    if len(tail) >= 4:
        out["nodes"] = struct.unpack_from("<I", tail, 0)[0]
    for name in SECTIONS:
        at = tail.find(name + b"\x01")
        if at >= 0:
            out["sections"][name.decode()] = at
    return out
