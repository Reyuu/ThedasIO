# msh decode, no bpy, Mesh/Chunk -> plain dicts

import struct

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


INDEX_FORMAT_U32 = 1


# The file serializes its own index width: 8032 GFF_MESH_INDEXFORMAT on the mesh
# (mirrored per chunk by 8004 GFF_MESH_CHUNK_INDEXFORMAT), 0 = u16, 1 = u32.
# Confirmed against the corpus -- zz_wrf_rc_0.msh / zz_wrf_bc_c_0.msh are the only
# shipped meshes with 8032 == 1, and they are the only ones over 65535 verts.
#
# Prefer it: it is what the engine unpacks with. Fall back to the vertex count
# only where the field is absent or non-binary, so hand-built fixtures and any
# file whose field we do not understand still decode.
def index_width(verts: int, index_format=None) -> int:
    if index_format is not None:
        fmt = index_format[1] if isinstance(index_format, (list, tuple)) else index_format
        if fmt in (0, INDEX_FORMAT_U32):
            return 4 if fmt == INDEX_FORMAT_U32 else 2
    return 4 if verts > 0xFFFF else 2


def chunk_index_width(chunk, mesh=None) -> int:
    # 8004 is per chunk and mirrors 8032; trust whichever is present.
    fmt = chunk.cells.get(8004)
    if fmt is None and mesh is not None:
        fmt = mesh.index_format
    return index_width(chunk.cells.get(8001, 0), fmt)


def chunk_index_format(chunk, mesh=None, width=None) -> int:
    # the field to store back, so a widened chunk stops contradicting its blob
    if width is None:
        width = chunk_index_width(chunk, mesh)
    return INDEX_FORMAT_U32 if width == 4 else 0


def weld_positions(positions):
    # exact dupes only, export expands back through the map
    seen = {}
    unique = []
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


def _decode_row(dtype, blob, offset):
    fmt = FORMATS.get(dtype)
    if fmt is None:
        raise ValueError(f"no decoder for declarator type {dtype}")
    size = struct.calcsize(fmt)
    return list(struct.unpack_from(fmt, blob, offset)), size


def _norm8_rows(rows, start: int = 0) -> list:
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


def _pad4(rows, verts: int, fill) -> list:
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


def _groups(weights, bones, verts: int) -> dict:
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


def _row_format(chunk, stride: int):
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


def decode_chunk(mesh, chunk):
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
    normals = []
    uvs = {}
    tangents = {}
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
                uvs.setdefault(d.usage_index, []).extend(
                    [(r[start], 1.0 - r[start + 1]) for r in rows]
                )
            elif d.usage in (USAGE_TANGENT, USAGE_BINORMAL):
                tangents.setdefault(d.usage, []).extend(
                    _norm8_rows(rows, start)
                    if d.dtype == 8
                    else [r[start : start + comps] for r in rows]
                )
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
                uvs.setdefault(d.usage_index, []).extend([(r[0], 1.0 - r[1]) for r in rows])
            elif d.usage in (USAGE_TANGENT, USAGE_BINORMAL):
                tangents.setdefault(d.usage, []).extend(
                    _norm8_rows(rows) if d.dtype == 8 else [tuple(r) for r in rows]
                )
            elif d.usage == USAGE_BLENDWEIGHT:
                weights_rows = [r[:4] for r in rows]
            elif d.usage == USAGE_BLENDINDICES:
                bones_rows = [r[:4] for r in rows]

    groups = _groups(_pad4(weights_rows, verts, 0.0), _pad4(bones_rows, verts, 0), verts)
    tris = list(zip(indices[0::3], indices[1::3], indices[2::3]))

    return {
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "tangents": tangents,
        "triangles": tris,
        "groups": groups,
        "vert_count": verts,
    }


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2.0 * (y * vz - z * vy), 2.0 * (z * vx - x * vz), 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


def triangulate_loops(polys):
    # fan triangulate, keep degenerates for the checker to flag
    out = []
    for poly in polys:
        corners = list(poly)
        for i in range(1, len(corners) - 1):
            out.append((corners[0], corners[i], corners[i + 1]))
    return out


IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)


def triangulate_faces(polys):
    # same fan split but for plain index lists
    out = []
    for poly in polys:
        vs = list(poly)
        for i in range(1, len(vs) - 1):
            out.append((vs[0], vs[i], vs[i + 1]))
    return out


def node_local(values):
    trsl = values.get(6047) or (0.0, 0.0, 0.0, 1.0)
    rota = values.get(6048) or IDENTITY_QUAT
    return (float(trsl[0]), float(trsl[1]), float(trsl[2])), tuple(float(x) for x in rota)


def bone_frame(quat) -> tuple:
    # MMH runs a bone along its node's local X, and Blender wants that along Y.
    # the remaining two axes have exactly one right-handed assignment:
    # node Z -> bone X, node Y -> bone Z. the other permutation is left-handed
    # on every bone in the game data, which would mirror the mesh.
    return (
        quat_rotate(quat, (0.0, 0.0, 1.0)),
        quat_rotate(quat, (1.0, 0.0, 0.0)),
        quat_rotate(quat, (0.0, 1.0, 0.0)),
    )


def compose(parent, local):
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


def flatten(tree):
    out = []

    def visit(node, parent, transform):
        local = node_local(node.values)
        world = compose(transform, local) if parent is not None else local
        out.append((node, parent, world[0], world[1]))
        for child in node.children:
            visit(child, node, world)

    visit(tree, None, ((0.0, 0.0, 0.0), IDENTITY_QUAT))
    return out


def node_paths(tree):
    def walk(node, path):
        yield node, path
        for i, child in enumerate(node.children):
            yield from walk(child, path + (i,))

    return walk(tree, ())


def bone_names(tree):
    names = {}
    for node, _, _, _ in flatten(tree):
        index = node.values.get(6254)
        if node.kind == "node" and isinstance(index, int) and index >= 0 and index not in names:
            names[index] = node.name
    return names
