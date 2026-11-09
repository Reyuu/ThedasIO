# Blender-side collision proxies for a ``.phy``.

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Matrix

from .description import PhyReader

COLLECTION = "ThedasIO Collision"
MASK_PREFIX = "thedas_io_phy_mask_"
SIDES = 12


def _capsule(radius: float | None, height: float | None) -> tuple[list, list]:
    r = float(radius) if radius else 0.05
    h = float(height) if height else 0.0
    cap = 4
    rings = []
    for i in range(cap + 1):  # -X cap, pole to equator
        a = (math.pi / 2) * i / cap
        rings.append((-h / 2 - r * math.cos(a), r * math.sin(a)))
    for i in range(cap + 1):  # +X cap, equator to pole
        a = (math.pi / 2) * (cap - i) / cap
        rings.append((h / 2 + r * math.cos(a), r * math.sin(a)))

    verts, faces = [], []
    for x, rad in rings:
        for j in range(SIDES):
            t = 2 * math.pi * j / SIDES
            verts.append((x, rad * math.cos(t), rad * math.sin(t)))
    for i in range(len(rings) - 1):
        for j in range(SIDES):
            k = (j + 1) % SIDES
            a, b = i * SIDES, (i + 1) * SIDES
            faces.append((a + j, a + k, b + k, b + j))
    return verts, faces


def _sphere(radius: float | None) -> tuple[list, list]:
    r = float(radius) if radius else 0.05
    half = SIDES // 2
    verts, faces = [], []
    for i in range(half + 1):
        a = math.pi * i / half
        x = r * math.cos(a)
        rad = r * math.sin(a)
        for j in range(SIDES):
            t = 2 * math.pi * j / SIDES
            verts.append((x, rad * math.cos(t), rad * math.sin(t)))
    for i in range(half):
        for j in range(SIDES):
            k = (j + 1) % SIDES
            faces.append((i * SIDES + j, i * SIDES + k, (i + 1) * SIDES + k, (i + 1) * SIDES + j))
    return verts, faces


def _box(radius: float | None) -> tuple[list, list]:
    s = (float(radius) if radius else 0.25) / 2
    verts = [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return verts, faces


def _mesh_from(nxs_mesh: Any) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(f"thedas_io_proxy_mesh_{nxs_mesh.vertex_count}")
    mesh.from_pydata(nxs_mesh.vertices, [], nxs_mesh.tris)
    mesh.update()
    return mesh


def _mesh_for(shape: Any) -> bpy.types.Mesh:
    kind = shape.kind
    if kind in ("mshs", "mesh") and shape.mesh is not None:
        return _mesh_from(shape.mesh)
    if kind in ("caps", "capsule"):
        verts, faces = _capsule(shape.radius, shape.height)
    elif kind in ("sphs", "sphere"):
        verts, faces = _sphere(shape.radius)
    else:
        verts, faces = _box(shape.radius)
    mesh = bpy.data.meshes.new(f"thedas_io_proxy_{kind}")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _box_verts(size: Any) -> list:
    s = size / 2.0
    return [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]


def _box_faces() -> list:
    return [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]


def _mesh_for_kind(
    kind: str, radius: float | None = None, height: float | None = None, size: Any = None
) -> bpy.types.Mesh:
    if kind in ("caps", "capsule"):
        verts, faces = _capsule(radius, height)
    elif kind in ("sphs", "sphere"):
        verts, faces = _sphere(radius)
    elif kind in ("mshs", "mesh"):
        verts, faces = _box_verts(size or 0.2), _box_faces()
    else:
        verts, faces = _box(size or radius or 0.25)
    mesh = bpy.data.meshes.new(f"thedas_io_proxy_{kind}")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _collection(context: Any) -> Any:
    existing = bpy.data.collections.get(COLLECTION)
    if existing is None:
        existing = bpy.data.collections.new(COLLECTION)
    if existing.name not in {c.name for c in context.collection.children}:
        try:
            context.collection.children.link(existing)
        except (RuntimeError, TypeError):
            pass
    return existing


def _bone_parent_inverse(bone: Any) -> Matrix:
    tail = Matrix.Translation((0.0, bone.length, 0.0))
    parent = bone.matrix_local @ tail
    return parent.inverted_safe() @ Matrix.Translation(bone.head_local)


def _match_bone(bones: dict, name: Any) -> Any:
    # a .phy repeats the mmh's node names and may disagree with the armature on
    # casing, so match insensitively and keep the armature's own spelling
    if not name:
        return None
    if name in bones:
        return name
    lowered = str(name).lower()
    return next((key for key in bones if str(key).lower() == lowered), None)


def build_proxies(context: Any, reader: PhyReader, bones: Any, owner: str = "") -> list:
    if not reader.shapes:
        return []
    collection = _collection(context)
    made = []
    for cobj in reader.objects:
        for shape in cobj.shapes:
            mesh = _mesh_for(shape)
            obj = bpy.data.objects.new(shape.name or f"collision_{shape.type_id}", mesh)
            collection.objects.link(obj)

            # the file's transform is the object's own basis for every kind.
            # A mesh shape's vertices are centred on its own origin (a 12x16 m
            # collision mesh sits at +-0.1 m), so POSITION is what puts it on
            # the bone: reading the object back gives the file's own values,
            # where forcing the location to zero rewrote POSITION on export.
            obj.location = tuple(shape.position or (0.0, 0.0, 0.0))[:3]
            rot = tuple(shape.rotation or (0.0, 0.0, 0.0, 1.0))
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = (rot[3], rot[0], rot[1], rot[2])

            bone_name = _match_bone(bones, shape.bone)
            parent = bones.get(bone_name) if bone_name else None
            if parent is not None:
                obj.parent = parent
                obj.parent_type = "BONE"
                obj.parent_bone = bone_name
                # keep the rig link (so the proxy follows animation) while the
                # object's own basis stays the file's
                bone = parent.data.bones.get(bone_name)
                if bone is not None:
                    obj.matrix_parent_inverse = _bone_parent_inverse(bone)

            obj["thedas_io_phy_path"] = ",".join(str(i) for i in shape.path)
            obj["thedas_io_phy_kind"] = shape.kind
            obj["thedas_io_phy_name"] = shape.name or ""
            obj["thedas_io_phy_bone"] = bone_name or ""
            obj["thedas_io_phy_object_type"] = cobj.obj_type if cobj.obj_type is not None else 0
            obj["thedas_io_phy_group"] = cobj.group if cobj.group is not None else 0
            if shape.radius is not None:
                obj["thedas_io_phy_radius"] = float(shape.radius)
            if shape.height is not None:
                obj["thedas_io_phy_height"] = float(shape.height)
            if shape.box_dim is not None:
                obj["thedas_io_phy_box_dim"] = tuple(float(v) for v in shape.box_dim[:3])
            for short, value in shape.masks.items():
                obj[f"{MASK_PREFIX}{short}"] = int(value)
            if owner:
                obj["thedas_io_phy_owner"] = owner
            made.append(obj)
    return made


def default_name(kind: str, bone: str = "") -> str:
    label = {
        "caps": "Capsule",
        "capsule": "Capsule",
        "sphs": "Sphere",
        "sphere": "Sphere",
        "boxs": "Box",
        "box": "Box",
        "mshs": "Mesh",
        "mesh": "Mesh",
    }.get(kind, "Shape")
    return f"{label}_{bone}" if bone else label


def add_proxy_for(context: Any, spec: dict, arm_obj: Any, bone: Any, owner: str = "") -> bpy.types.Object:
    kind = str(spec.get("kind") or "capsule")
    mesh = _mesh_for_kind(kind, spec.get("radius"), spec.get("height"), spec.get("size"))
    name = str(spec.get("name") or default_name(kind, spec.get("bone") or ""))
    obj = bpy.data.objects.new(name, mesh)
    _collection(context).objects.link(obj)
    pos = tuple(spec.get("position") or (0.0, 0.0, 0.0))[:3]
    obj.location = pos
    rot = tuple(spec.get("rotation") or (0.0, 0.0, 0.0, 1.0))
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (rot[3], rot[0], rot[1], rot[2])
    obj.parent = arm_obj
    obj.parent_type = "BONE"
    obj.parent_bone = bone.name
    obj.matrix_parent_inverse = _bone_parent_inverse(bone)
    obj["thedas_io_phy_path"] = ""
    obj["thedas_io_phy_new"] = True
    obj["thedas_io_phy_kind"] = "caps" if kind == "capsule" else kind
    obj["thedas_io_phy_name"] = name
    obj["thedas_io_phy_bone"] = spec.get("bone") or ""
    obj["thedas_io_phy_radius"] = float(spec.get("radius") or 0.05)
    if kind == "capsule":
        obj["thedas_io_phy_height"] = float(spec.get("height") or 0.0)
    if kind == "box" and spec.get("box_dim"):
        obj["thedas_io_phy_box_dim"] = tuple(float(v) for v in spec["box_dim"][:3])
    if owner:
        obj["thedas_io_phy_owner"] = owner
    return obj


def _path_of(obj: Any) -> tuple:
    raw = obj.get("thedas_io_phy_path") or ""
    try:
        return tuple(int(p) for p in str(raw).split(",") if p != "")
    except ValueError:
        return ()


def _values(obj: Any) -> dict:
    out: dict = {}
    name = obj.get("thedas_io_phy_name")
    if name is not None:
        out["name"] = name

    # The file stores a shape's transform in its bone's space, and the proxy's
    # own basis is exactly that: build_proxies wrote the file's values there,
    # and the viewport moves them there. Reading the world matrix instead would
    # need the depsgraph to have run (right after an import it has not, so every
    # shape would look edited and every untouched .phy would be rewritten).
    loc = obj.location
    quat = obj.rotation_quaternion
    scale = obj.scale
    out["position"] = (loc[0], loc[1], loc[2], 1.0)
    out["rotation"] = (quat[1], quat[2], quat[3], quat[0])

    # dimensions are authored in Blender by scaling the proxy, so the export
    # reads the stored size through the object's own scale. the file's capsule
    # runs along X: radius is across Y/Z, the cylinder length is along X.
    kind = obj.get("thedas_io_phy_kind")
    sx, sy, sz = scale
    radius = obj.get("thedas_io_phy_radius")
    if radius is not None:
        out["radius"] = float(radius) * (sy + sz) / 2.0 if kind in ("caps",) else float(radius) * sx
    height = obj.get("thedas_io_phy_height")
    if height is not None and kind in ("caps",):
        out["height"] = float(height) * sx
    box = obj.get("thedas_io_phy_box_dim")
    if box is not None:
        out["box_dim"] = tuple(float(v) * s for v, s in zip(box, (sx, sy, sz)))

    # strip the prefix, not a suffix: mask names carry underscores of their
    # own, as in static_geometry
    for key in list(obj.keys()):
        if key.startswith(MASK_PREFIX):
            out[f"mask_{key[len(MASK_PREFIX) :]}"] = int(obj[key])
    if obj.get("thedas_io_phy_object_type") is not None:
        out["obj_type"] = int(obj["thedas_io_phy_object_type"])
    if obj.get("thedas_io_phy_group") is not None:
        out["group"] = int(obj["thedas_io_phy_group"])
    return out


def _sequence(value: Any) -> list | None:
    # a custom property holding a vector hands back an IDPropertyArray, which
    # has to_list(); a tuple does not, and the two never compare equal
    if isinstance(value, (list, tuple)):
        return list(value)
    to_list = getattr(value, "to_list", None)
    return list(to_list()) if callable(to_list) else None


def _same(a: Any, b: Any) -> bool:
    left, right = _sequence(a), _sequence(b)
    if left is not None or right is not None:
        if left is None or right is None or len(left) != len(right):
            return False
        return all(_same(x, y) for x, y in zip(left, right))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-6
        except (TypeError, ValueError):
            return a == b
    return a == b


def proxies_for(item: Any) -> list:
    return list(item.get("collision") or [])


def mark_snapshots(objects: list) -> None:
    for obj in objects:
        for key, value in _values(obj).items():
            obj[f"thedas_io_phy_base_{key}"] = value


def edits_for(item: Any) -> dict:
    edits: dict = {}
    for obj in proxies_for(item):
        if obj.get("thedas_io_phy_new"):
            continue
        path = _path_of(obj)
        if not path:
            continue
        changed = {}
        for key, value in _values(obj).items():
            base = obj.get(f"thedas_io_phy_base_{key}")
            if base is None or not _same(base, value):
                changed[key] = value
        if changed:
            edits.setdefault(path, {}).update(changed)
    return edits


def _adopted_spec(obj: Any, arm_obj: Any, bone_name: str) -> dict:
    from mathutils import Vector

    bone = arm_obj.data.bones[bone_name]
    arm_inv = arm_obj.matrix_world.inverted()
    m = arm_inv @ obj.matrix_world
    q = m.to_quaternion()
    base = tuple(m.translation)
    pos = (base[0] - bone.head_local[0], base[1] - bone.head_local[1], base[2] - bone.head_local[2])

    kind = str(obj.get("thedas_io_phy_kind") or "box")
    kind = {
        "capsule": "capsule",
        "caps": "capsule",
        "sphere": "sphere",
        "sphs": "sphere",
        "box": "box",
        "boxs": "box",
    }.get(kind, "box")

    spec: dict[str, Any] = {
        "bone": bone_name,
        "kind": kind,
        "position": pos,
        "rotation": (q[1], q[2], q[3], q[0]),
        "name": default_name(kind, bone_name),
    }
    if kind == "box":
        # an armature-aligned box that covers the mesh in its current (world)
        # pose, so scale and rotation are both honoured
        if hasattr(obj.data, "bound_box"):
            corners = [arm_inv @ (obj.matrix_world @ Vector(c)) for c in obj.data.bound_box]
            lo = [min(c[i] for c in corners) for i in range(3)]
            hi = [max(c[i] for c in corners) for i in range(3)]
            spec["box_dim"] = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            spec["position"] = (
                (lo[0] + hi[0]) / 2 - bone.head_local[0],
                (lo[1] + hi[1]) / 2 - bone.head_local[1],
                (lo[2] + hi[2]) / 2 - bone.head_local[2],
            )
            spec["rotation"] = (0.0, 0.0, 0.0, 1.0)
    else:
        spec["radius"] = float(obj.get("thedas_io_phy_radius") or 0.05)
        if kind == "capsule":
            spec["height"] = float(obj.get("thedas_io_phy_height") or 0.0)
    return spec


def adopted_specs(item: Any) -> list:
    import bpy

    arm = item.get("armature")
    if arm is None:
        return []
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return []
    owner = item.get("file", "")
    tracked = {o.name for o in proxies_for(item)}
    specs = []
    for obj in collection.objects:
        # proxies belong to the item that made them; one dropped in by hand has
        # no owner at all, and that is exactly the case worth adopting
        if obj.get("thedas_io_phy_owner") not in (None, "", owner):
            continue
        bone_name = str(obj.get("thedas_io_phy_bone") or "")
        if not bone_name or bone_name not in arm.data.bones:
            continue
        if obj.name in tracked:
            continue
        specs.append(_adopted_spec(obj, arm, bone_name))
    return specs
