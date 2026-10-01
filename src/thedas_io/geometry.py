# msh decode, no bpy, Mesh/Chunk -> plain dicts

import struct

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
    15: "<2e",
    16: "<4e",
}

USAGE_POSITION = 0
USAGE_BLENDWEIGHT = 1
USAGE_BLENDINDICES = 2
USAGE_NORMAL = 3
USAGE_TEXCOORD = 5
USAGE_TANGENT = 6
USAGE_BINORMAL = 7


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


def decode_chunk(mesh, chunk):
    if chunk.cells.get(8003, 0) != 0:
        raise ValueError(f"chunk {chunk.name}: only trilist primitive 0 supported")
    stride = chunk.stride
    if 8000 in chunk.cells and stride != chunk.cells[8000]:
        raise ValueError(f"chunk {chunk.name}: stride mismatch")

    verts = chunk.cells.get(8001, 0)
    v0 = chunk.cells.get(8006, 0)
    blob = bytes(mesh.vertex_data[v0 : v0 + verts * stride])

    ncounts = chunk.cells.get(8002, 0)
    if ncounts > 0xFFFF:
        width = 4
    else:
        width = 2
    i0 = chunk.cells.get(8009, 0) * width
    iblob = bytes(mesh.index_data[i0 : i0 + ncounts * width])

    if width == 4:
        indices = list(struct.unpack(f"<{ncounts}I", iblob))
    else:
        indices = list(struct.unpack(f"<{ncounts}H", iblob))
    if len(indices) % 3:
        raise ValueError(f"chunk {chunk.name}: indices not a multiple of 3")

    positions = []
    normals = []
    uvs = {}
    tangents = {}
    w_by_vert = {}
    b_by_vert = {}

    for d in chunk.declarators:
        if d.is_terminator:
            continue
        info = DECLTYPE.get(d.dtype, (None, None))
        size = info[1]
        if size is None or d.offset + size > stride:
            raise ValueError(f"chunk {chunk.name}: declarator overruns stride")
        for v in range(verts):
            values, _ = _decode_row(d.dtype, blob, v * stride + d.offset)
            if d.usage == USAGE_POSITION:
                positions.append((values[0], values[1], values[2]))
            elif d.usage == USAGE_NORMAL:
                normals.append((values[0], values[1], values[2]))
            elif d.usage == USAGE_TEXCOORD:
                if d.usage_index not in uvs:
                    uvs[d.usage_index] = []
                uvs[d.usage_index].append((values[0], 1.0 - values[1]))
            elif d.usage == 6 or d.usage == 7:
                if d.usage not in tangents:
                    tangents[d.usage] = []
                tangents[d.usage].append(tuple(values))
            elif d.usage == USAGE_BLENDWEIGHT:
                w_by_vert[v] = list(values)
            elif d.usage == USAGE_BLENDINDICES:
                bl = []
                for x in values:
                    bl.append(int(x))
                b_by_vert[v] = bl

    groups = {}
    for v in range(verts):
        vals = w_by_vert.get(v)
        if not vals:
            vals = [0.0] * 4
        vals = vals[:4]
        bones = b_by_vert.get(v)
        if not bones:
            bones = [0] * 4
        bones = bones[:4]
        total = sum(vals)
        if not total:
            total = 1.0
        for i in range(4):
            weight = vals[i]
            bone = bones[i]
            if weight > 1e-4:
                if bone not in groups:
                    groups[bone] = []
                groups[bone].append((v, weight / total))

    tris = []
    for i in range(0, len(indices), 3):
        tris.append((indices[i], indices[i + 1], indices[i + 2]))

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


def compose(parent, local):
    # legacy MSH_Tool rule
    (ploc, prot), (lloc, lrot) = parent, local
    return (
        (
            ploc[0] + quat_rotate(prot, lloc)[0],
            ploc[1] + quat_rotate(prot, lloc)[1],
            ploc[2] + quat_rotate(prot, lloc)[2],
        ),
        quat_mul(lrot, prot),
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
