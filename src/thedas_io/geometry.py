# msh decode, no bpy, Mesh/Chunk -> plain dicts

import struct
from collections.abc import Iterator
from typing import Any

import numpy as np

from .msh.description import DECLTYPE

FORMATS = {
    0: "<f",
    1: "<2f",
    2: "<3f",
    3: "<4f",
    4: "<4B",
    5: "<4B",
    6: "<2h",
    7: "<4h",
    8: "<4B",  # UBYTE4N
    15: "<2e",
    16: "<4e",
}

# dtype -> (numpy scalar dtype, components). Mirrors FORMATS: a chunk we could
# decode before still decodes, and an unknown dtype is still refused.
_NUMPY = {
    0: ("<f4", 1),
    1: ("<f4", 2),
    2: ("<f4", 3),
    3: ("<f4", 4),
    4: ("u1", 4),
    5: ("u1", 4),
    6: ("<i2", 2),
    7: ("<i2", 4),
    8: ("u1", 4),
    15: ("<f2", 2),
    16: ("<f2", 4),
}

USAGE_POSITION = 0
USAGE_BLENDWEIGHT = 1
USAGE_BLENDINDICES = 2
USAGE_NORMAL = 3
USAGE_TEXCOORD = 5
USAGE_TANGENT = 6
USAGE_BINORMAL = 7
USAGE_COLOR = 10

# declarator types the colour channel is read from and written back as. Bytes
# are D3DCOLOR/UBYTE4 (BGRA); the float types are RGB(A), or the half4 DAO uses
# for water/lava vertex data.
COLOR_DTYPES = (2, 3, 4, 5, 16)
COLOR_ATTRIBUTE = "thedas_io_color"


INDEX_FORMAT_U32 = 1


# The file serializes its own index width: 8032 GFF_MESH_INDEXFORMAT on the mesh
# (mirrored per chunk by 8004 GFF_MESH_CHUNK_INDEXFORMAT), 0 = u16, 1 = u32.
# Confirmed against the corpus -- zz_wrf_rc_0.msh / zz_wrf_bc_c_0.msh are the only
# shipped meshes with 8032 == 1, and they are the only ones over 65535 verts.
#
# Prefer it: it is what the engine unpacks with. Fall back to the vertex count
# only where the field is absent or non-binary, so hand-built fixtures and any
# file whose field we do not understand still decode.
def index_width(verts: int, index_format: Any = None) -> int:
    if index_format is not None:
        fmt = index_format[1] if isinstance(index_format, (list, tuple)) else index_format
        if fmt in (0, INDEX_FORMAT_U32):
            return 4 if fmt == INDEX_FORMAT_U32 else 2
    return 4 if verts > 0xFFFF else 2


def chunk_index_width(chunk: Any, mesh: Any = None) -> int:
    # 8004 is per chunk and mirrors 8032; trust whichever is present.
    fmt = chunk.cells.get(8004)
    if fmt is None and mesh is not None:
        fmt = mesh.index_format
    return index_width(chunk.cells.get(8001, 0), fmt)


def chunk_index_format(chunk: Any, mesh: Any = None, width: int | None = None) -> int:
    # the field to store back, so a widened chunk stops contradicting its blob
    if width is None:
        width = chunk_index_width(chunk, mesh)
    return INDEX_FORMAT_U32 if width == 4 else 0


def weld_positions(positions: list) -> tuple[list, list]:
    # exact dupes only, export expands back through the map
    seen: dict = {}
    unique: list = []
    row_to_vert = []

    for p in positions:
        key = (p[0], p[1], p[2])
        v = seen.get(key)
        if v is None:
            v = len(unique)
            seen[key] = v
            unique.append(p)
        row_to_vert.append(v)
    return unique, row_to_vert


def _decode_row(dtype: int, blob: bytes, offset: int) -> tuple[list, int]:
    fmt = FORMATS.get(dtype)
    if fmt is None:
        raise ValueError(f"no decoder for declarator type {dtype}")
    size = struct.calcsize(fmt)
    return list(struct.unpack_from(fmt, blob, offset)), size


def _norm8_rows(rows: list, start: int = 0) -> list:
    # UBYTE4N (type 8): 0-255 bytes the engine normalises via
    # (value - 128) / 127. DA2 uses it for normals/tangents.
    return [
        (
            (int(r[start]) - 128) / 127,
            (int(r[start + 1]) - 128) / 127,
            (int(r[start + 2]) - 128) / 127,
        )
        for r in rows
    ]


def _color_rows(rows: list, start: int, dtype: int) -> list:
    # D3DCOLOR/UBYTE4 store BGRA; Blender wants RGBA 0..1. The float types are
    # RGBA already, and hold shader values (a water plane's 4th column is not
    # alpha), so they pass through untouched and unclamped.
    if dtype in (2, 3, 16):
        if dtype == 2:
            return [(r[start], r[start + 1], r[start + 2], 1.0) for r in rows]
        return [tuple(r[start : start + 4]) for r in rows]
    return [
        (r[start + 2] / 255, r[start + 1] / 255, r[start] / 255, r[start + 3] / 255) for r in rows
    ]


def decl_components(dtype: int) -> int:
    # how many values one declarator writes per vertex
    spec = _NUMPY.get(dtype)
    return spec[1] if spec else 0


# DA2 environment meshes store a tangent frame in texcoord slots; the channels
# are 4 normalized bytes with the direction in xyz and a sign in w
FRAME_DTYPE = 8
FRAME_NAMES = {0: "Frame Normal", 1: "Frame Tangent", 3: "Frame Binormal"}


def frame_name(usage_index: Any) -> str:
    return FRAME_NAMES.get(usage_index, f"Frame {usage_index}")


def chunk_blob(mesh: Any, chunk: Any) -> bytes:
    # the chunk's own vertex region, byte-addressed from v0
    stride = chunk.stride
    verts = chunk.cells.get(8001, 0)
    v0 = chunk.cells.get(8006, 0)
    return bytes(mesh.vertex_data[v0 : v0 + verts * stride])


def _looks_like_direction(blob: bytes, verts: int, stride: int, d: Any) -> bool:
    # a UV channel would not decode to unit vectors as (byte - 128) / 127
    if d.dtype != FRAME_DTYPE or decl_components(d.dtype) < 3 or not verts:
        return False
    step = max(1, verts // 128)
    hits = total = 0
    for v in range(0, verts, step):
        vals, _ = _decode_row(d.dtype, blob, v * stride + d.offset)
        x, y, z = ((vals[i] - 128) / 127 for i in range(3))
        total += 1
        if 0.9 < (x * x + y * y + z * z) ** 0.5 < 1.1:
            hits += 1
    return total > 0 and hits / total >= 0.9


def split_texcoords(chunk: Any, blob: bytes = b"") -> tuple[list, list]:
    """(real UV pairs, frame channels) for one chunk, in declarator order.

    A 4-value texcoord declarator holds two texture coordinate sets, not one:
    DA2 packs the second set there. Byte channels that decode to unit vectors
    are not UVs at all but the mesh's normal/tangent/binormal, and per chunk
    they all are: an unused binormal sits at zero beside its siblings.
    """
    stride = chunk.stride
    verts = chunk.cells.get(8001, 0)
    tex = [d for d in chunk.declarators if not d.is_terminator and d.usage == USAGE_TEXCOORD]
    frames: list = []
    if blob and any(_looks_like_direction(blob, verts, stride, d) for d in tex):
        frames = [d for d in tex if d.dtype == FRAME_DTYPE]
    frame_ids = {id(d) for d in frames}
    real: list = []
    layer = 0
    for d in tex:
        if id(d) in frame_ids:
            continue
        comps = decl_components(d.dtype)
        for offset in range(0, comps - comps % 2, 2):
            real.append((layer, d, offset))
            layer += 1
    return real, frames


def uv_pairs(chunk: Any, blob: bytes = b"") -> list:
    # (layer, declarator, component offset) for every real UV pair
    return split_texcoords(chunk, blob)[0]


def _decode_uvs(blob: bytes, verts: int, stride: int, slots: list) -> dict:
    uvs: dict = {}
    for layer, d, offset in slots:
        fmt = _pair_format(d.dtype)
        if fmt is None:
            raise ValueError(f"no decoder for a texcoord pair of type {d.dtype}")
        # offset counts components, the blob is addressed in bytes
        start = d.offset + (offset // 2) * struct.calcsize(fmt)
        uvs[layer] = [
            (values[0], 1.0 - values[1])
            for values in (struct.unpack_from(fmt, blob, v * stride + start) for v in range(verts))
        ]
    return uvs


def _decode_frames(blob: bytes, verts: int, stride: int, frames: list) -> list:
    # biased floats, the same space the colour mask and the attribute use
    return [
        (
            d.usage_index,
            [
                values
                for values in (
                    tuple(
                        (v - 128) / 127
                        for v in _decode_row(d.dtype, blob, v * stride + d.offset)[0]
                    )
                    for v in range(verts)
                )
            ],
        )
        for d in frames
    ]


def _pair_format(dtype: int) -> str | None:
    # two components of one declarator, in the declarator's own element type
    fmt = FORMATS.get(dtype)
    if fmt is None:
        return None
    element = fmt[-1]
    count = int(fmt[1:-1] or 1)
    return f"<2{element}" if count >= 2 else None


def _pad4(rows: list | None, verts: int, fill: Any) -> list:
    # the common chunk already has four weight/index components per row; only
    # copy when it has fewer (or an unexpected extra), so 78k rows do not turn
    # into 156k throwaway lists
    if rows is not None:
        n = len(rows[0]) if rows else 0
        if n == 4:
            return rows
        if n > 4:
            return [row[:4] for row in rows]
    out = [[fill, fill, fill, fill] for _ in range(verts)]
    if rows is not None:
        for v, row in enumerate(rows):
            out[v][: len(row)] = row
    return out


def _groups(weights: list, bones: list, verts: int) -> dict:
    groups: dict = {}
    for v in range(verts):
        vals = weights[v]
        total = sum(vals)
        if not total:
            total = 1.0
        row_bones = bones[v]
        for i in range(4):
            weight = vals[i]
            if weight > 1e-4:
                groups.setdefault(row_bones[i], []).append((v, weight / total))
    return groups


def _row_format(chunk: Any, stride: int) -> tuple[str, list] | None:
    parts: list[str] = []
    slices = []
    cursor_bytes = 0
    components = 0
    decls = sorted((d for d in chunk.declarators if not d.is_terminator), key=lambda d: d.offset)
    for d in decls:
        fmt = FORMATS.get(d.dtype)
        spec = _NUMPY.get(d.dtype)
        if fmt is None or spec is None:
            return None
        size = struct.calcsize(fmt)
        if d.offset < cursor_bytes or d.offset + size > stride:
            return None
        if d.offset > cursor_bytes:
            parts.append(f"{d.offset - cursor_bytes}x")
        parts.append(fmt[1:])  # little-endian is declared once, below
        slices.append((components, d, spec[1]))
        components += spec[1]
        cursor_bytes = d.offset + size
    if cursor_bytes < stride:
        parts.append(f"{stride - cursor_bytes}x")
    return "<" + "".join(parts), slices


def decode_chunk(mesh: Any, chunk: Any) -> dict:
    if chunk.cells.get(8003, 0) != 0:
        raise ValueError(f"chunk {chunk.name}: only trilist primitive 0 supported")
    stride = chunk.stride
    if 8000 in chunk.cells and stride != chunk.cells[8000]:
        raise ValueError(f"chunk {chunk.name}: stride mismatch")

    verts = chunk.cells.get(8001, 0)
    v0 = chunk.cells.get(8006, 0)
    blob = bytes(mesh.vertex_data[v0 : v0 + verts * stride])
    if len(blob) != verts * stride:
        # a truncated blob used to surface as a struct.error from whichever
        # row ran off the end; name the shortfall instead
        raise ValueError(
            f"chunk {chunk.name}: vertex blob holds {len(blob)}B, "
            f"{verts} rows need {verts * stride}B"
        )

    ncounts = chunk.cells.get(8002, 0)
    width = chunk_index_width(chunk, mesh)
    i0 = chunk.cells.get(8009, 0) * width
    iblob = bytes(mesh.index_data[i0 : i0 + ncounts * width])

    if len(iblob) != ncounts * width:
        raise ValueError(
            f"chunk {chunk.name}: index blob holds {len(iblob)}B, "
            f"{verts} verts need {ncounts * width}B at width {width}"
        )
    if width == 4:
        indices = np.frombuffer(iblob, dtype="<u4").tolist()
    else:
        indices = np.frombuffer(iblob, dtype="<u2").tolist()
    if len(indices) % 3:
        raise ValueError(f"chunk {chunk.name}: indices not a multiple of 3")

    positions = []
    normals: list = []
    uvs: dict = {}
    tangents: dict = {}
    colors: list = []
    weights_rows = None
    bones_rows = None

    layout = _row_format(chunk, stride)
    if layout is not None:
        row_fmt, slices = layout
        # one C-level unpack per vertex for every declarator at once, instead
        # of struct.unpack_from per declarator per vertex
        rows = list(struct.iter_unpack(row_fmt, blob))
        for start, d, comps in slices:
            if d.usage == USAGE_POSITION:
                positions = [(r[start], r[start + 1], r[start + 2]) for r in rows]
            elif d.usage == USAGE_NORMAL:
                normals = (
                    _norm8_rows(rows, start)
                    if d.dtype == 8
                    else [(r[start], r[start + 1], r[start + 2]) for r in rows]
                )
            elif d.usage == USAGE_TEXCOORD:
                pass  # decoded below, in declarator order (see uv_pairs)
            elif d.usage in (USAGE_TANGENT, USAGE_BINORMAL):
                tangents.setdefault(d.usage, []).extend(
                    _norm8_rows(rows, start)
                    if d.dtype == 8
                    else [r[start : start + comps] for r in rows]
                )
            elif d.usage == USAGE_COLOR:
                colors = _color_rows(rows, start, d.dtype)
            elif d.usage == USAGE_BLENDWEIGHT:
                weights_rows = [r[start : start + comps] for r in rows]
            elif d.usage == USAGE_BLENDINDICES:
                bones_rows = [r[start : start + comps] for r in rows]
    else:
        for d in chunk.declarators:
            if d.is_terminator:
                continue
            info = DECLTYPE.get(d.dtype, (None, None))
            size = info[1]
            if size is None or d.offset + size > stride:
                raise ValueError(f"chunk {chunk.name}: declarator overruns stride")
            spec = _NUMPY.get(d.dtype)
            if spec is None:
                raise ValueError(f"no decoder for declarator type {d.dtype}")
            dtype, comps = spec
            view = np.ndarray(
                shape=(verts, comps),
                dtype=dtype,
                buffer=blob,
                offset=d.offset,
                strides=(stride, np.dtype(dtype).itemsize),
            )
            rows = view.tolist()
            if d.usage == USAGE_POSITION:
                positions = [(r[0], r[1], r[2]) for r in rows]
            elif d.usage == USAGE_NORMAL:
                normals = _norm8_rows(rows) if d.dtype == 8 else [(r[0], r[1], r[2]) for r in rows]
            elif d.usage == USAGE_TEXCOORD:
                pass  # decoded below, in declarator order (see uv_pairs)
            elif d.usage in (USAGE_TANGENT, USAGE_BINORMAL):
                tangents.setdefault(d.usage, []).extend(
                    _norm8_rows(rows) if d.dtype == 8 else [tuple(r) for r in rows]
                )
            elif d.usage == USAGE_COLOR:
                colors = _color_rows(rows, 0, d.dtype)
            elif d.usage == USAGE_BLENDWEIGHT:
                weights_rows = [r[:4] for r in rows]
            elif d.usage == USAGE_BLENDINDICES:
                bones_rows = [r[:4] for r in rows]

    # UVs last, in declarator order: a pair-per-declarator assumption drops the
    # second set a 4-value texcoord carries. Byte channels holding a tangent
    # frame are split out here, so they never surface as texture coordinates.
    slots, frame_decls = split_texcoords(chunk, blob)
    if slots:
        uvs = _decode_uvs(blob, verts, stride, slots)
    frames = _decode_frames(blob, verts, stride, frame_decls) if frame_decls else []

    groups = _groups(_pad4(weights_rows, verts, 0.0), _pad4(bones_rows, verts, 0), verts)
    tris = list(zip(indices[0::3], indices[1::3], indices[2::3]))

    return {
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "tangents": tangents,
        "frames": frames,
        "colors": colors,
        "triangles": tris,
        "groups": groups,
        "vert_count": verts,
    }


def quat_mul(a: tuple, b: tuple) -> tuple:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q: tuple, v: tuple) -> tuple:
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2.0 * (y * vz - z * vy), 2.0 * (z * vx - x * vz), 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


def triangulate_loops(polys: list) -> list:
    # fan triangulate, keep degenerates for the checker to flag
    out = []
    for poly in polys:
        corners = list(poly)
        for i in range(1, len(corners) - 1):
            out.append((corners[0], corners[i], corners[i + 1]))
    return out


IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)


def triangulate_faces(polys: list) -> list:
    # same fan split but for plain index lists
    out = []
    for poly in polys:
        vs = list(poly)
        for i in range(1, len(vs) - 1):
            out.append((vs[0], vs[i], vs[i + 1]))
    return out


def node_local(values: dict) -> tuple:
    trsl = values.get(6047) or (0.0, 0.0, 0.0, 1.0)
    rota = values.get(6048) or IDENTITY_QUAT
    return (float(trsl[0]), float(trsl[1]), float(trsl[2])), tuple(float(x) for x in rota)


def bone_frame(quat: tuple) -> tuple:
    # MMH runs a bone along its node's local X, and Blender wants that along Y.
    # the remaining two axes have exactly one right-handed assignment:
    # node Z -> bone X, node Y -> bone Z. the other permutation is left-handed
    # on every bone in the game data, which would mirror the mesh.
    return (
        quat_rotate(quat, (0.0, 0.0, 1.0)),
        quat_rotate(quat, (1.0, 0.0, 0.0)),
        quat_rotate(quat, (0.0, 1.0, 0.0)),
    )


def compose(parent: tuple, local: tuple) -> tuple:
    # world = parent * local, so the parent applies after the local rotation.
    # the other order looks equivalent on a flat chain but drifts with depth:
    # on qm_uhm_basb_0 it put deep bones a mean 1.38m from the geometry they
    # deform, against 0.05m here.
    (ploc, prot), (lloc, lrot) = parent, local
    return (
        (
            ploc[0] + quat_rotate(prot, lloc)[0],
            ploc[1] + quat_rotate(prot, lloc)[1],
            ploc[2] + quat_rotate(prot, lloc)[2],
        ),
        quat_mul(prot, lrot),
    )


def flatten(tree: Any) -> list:
    out = []

    def visit(node: Any, parent: Any, transform: tuple) -> None:
        local = node_local(node.values)
        world = compose(transform, local) if parent is not None else local
        out.append((node, parent, world[0], world[1]))
        for child in node.children:
            visit(child, node, world)

    visit(tree, None, ((0.0, 0.0, 0.0), IDENTITY_QUAT))
    return out


def node_paths(tree: Any) -> Iterator[tuple[Any, tuple]]:
    def walk(node: Any, path: tuple) -> Iterator[tuple[Any, tuple]]:
        yield node, path
        for i, child in enumerate(node.children):
            yield from walk(child, path + (i,))

    return walk(tree, ())


def bone_names(tree: Any) -> dict:
    names = {}
    for node, _, _, _ in flatten(tree):
        index = node.values.get(6254)
        if node.kind == "node" and isinstance(index, int) and index >= 0 and index not in names:
            names[index] = node.name
    return names
