# A ``mshs`` collision shape stores its geometry as an ``NXS`` blob: the literal
# ``b"NXS"``, a version byte, the ``b"MESH"`` section tag, a fixed 28-byte header,
# then the arrays::
#
#     36                     A * 3  float32   vertex positions
#     36 + 12 * A            B * 3  indices   triangles, 1 byte if A <= 256
#     ...                    B * 3  indices                      else 2
#     end of indices         opaque tail       normals, sub-meshes
#
# ``A`` is the vertex count and ``B`` the triangle count; closed meshes hit
# ``B == 2A - 4`` exactly. Positions are the only array this module interprets.
# Everything after the indices is carried through byte for byte, so a mesh can be
# rescaled or moved without the collision structure that follows it shifting.
#
# Verified across all 624 mesh shapes in the DAO install: every stream parses,
# and under the index-width rule above no triangle references a vertex that does
# not exist.


from __future__ import annotations

import struct

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

    def __init__(self, raw: bytes, vertices, tris):
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

    def translated(self, offset) -> NxsMesh:
        dx, dy, dz = offset
        moved = [(x + dx, y + dy, z + dz) for x, y, z in self.vertices]
        return NxsMesh(_write(moved, self.tris, self.raw), moved, self.tris)

    def scaled(self, factor) -> NxsMesh:
        f = float(factor)
        scaled = [(x * f, y * f, z * f) for x, y, z in self.vertices]
        return NxsMesh(_write(scaled, self.tris, self.raw), scaled, self.tris)


def _index_width(verts: int) -> int:
    return 1 if verts <= U8_LIMIT else 2


def _write(vertices, tris, template: bytes) -> bytes:
    # Rebuild the stream from arrays, preserving template's header and tail.
    verts = len(vertices)
    tris = len(tris)
    width = _index_width(verts)
    start = HEADER_SIZE + 12 * verts
    idx_bytes = 3 * tris * width
    head = bytearray(template[:HEADER_SIZE])
    struct.pack_into("<II", head, OFF_VERTS, verts, tris)
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
