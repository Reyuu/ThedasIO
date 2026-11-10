# Blender-side collision proxies for a ``.phy``.

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Quaternion, Vector

from .description import PhyReader

COLLECTION = "Collision"
COLLECTION_TAG = "thedas_io_phy_owner"
MODEL_TAG = "thedas_io_model_owner"
PROXY_FRAME = "thedas_io_phy_frame"
PROXY_FRAME_VERSION = 2
MASK_PREFIX = "thedas_io_phy_mask_"
SIDES = 12

# node axes -> blender bone axes: node x -> bone y, node y -> bone z, node z -> bone x
NODE_TO_BONE = Matrix(
    (
        (0.0, 0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)
QP = Quaternion((0.5, 0.5, 0.5, 0.5))
QP_INV = Quaternion((0.5, -0.5, -0.5, -0.5))

# barely-there fill with an edge outline, so shapes read against the model
PROXY_MATERIAL = "ThedasIO Collision"
PROXY_ALPHA = 0.1
PROXY_COLOR = (1.0, 0.45, 0.15)


def proxy_material() -> Any:
    material = bpy.data.materials.get(PROXY_MATERIAL)
    if material is None:
        material = bpy.data.materials.new(PROXY_MATERIAL)
    material.diffuse_color = (*PROXY_COLOR, PROXY_ALPHA)  # type: ignore[assignment]  # bpy_prop_array is invariant
    try:
        material.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    material.use_nodes = True
    node_tree = material.node_tree
    bsdf = node_tree.nodes.get("Principled BSDF") if node_tree is not None else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*PROXY_COLOR, 1.0)
        bsdf.inputs["Alpha"].default_value = PROXY_ALPHA
    return material


def _dress(obj: Any) -> None:
    # shared material, edge outline, drawn through the model it collides for
    try:
        if not obj.data.materials:
            obj.data.materials.append(proxy_material())
        obj.show_in_front = True
        obj.show_wire = True
    except (AttributeError, ReferenceError, TypeError, RuntimeError):
        pass


def _capsule(
    radius: float | None, height: float | None, along_x: bool = False
) -> tuple[list, list]:
    # node space: a .phy capsule runs along Z, except on a model-space root bone
    r = float(radius) if radius else 0.05
    h = float(height) if height else 0.0
    if h <= r:
        return _sphere(radius)
    cap = 4
    rings = []
    for i in range(cap + 1):  # -Z cap, pole to equator
        a = (math.pi / 2) * i / cap
        rings.append((-h / 2 - r * math.cos(a), r * math.sin(a)))
    for i in range(cap + 1):  # +Z cap, equator to pole
        a = (math.pi / 2) * (cap - i) / cap
        rings.append((h / 2 + r * math.cos(a), r * math.sin(a)))

    verts, faces = [], []
    for z, rad in rings:
        for j in range(SIDES):
            t = 2 * math.pi * j / SIDES
            if along_x:
                verts.append((z, rad * math.cos(t), rad * math.sin(t)))
            else:
                verts.append((rad * math.cos(t), rad * math.sin(t), z))
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


def _mesh_from_geometry(verts: Any, tris: Any) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(f"thedas_io_proxy_mesh_{len(verts)}")
    mesh.from_pydata(list(verts), [], list(tris))
    mesh.update()
    return mesh


def object_geometry(obj: Any, selected_only: bool = False) -> tuple[list, list]:
    # triangle soup of an object's mesh, optionally only its selected faces
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "vertices"):
        return [], []
    try:
        mesh.calc_loop_triangles()
        triangles = list(mesh.loop_triangles)
    except (AttributeError, RuntimeError):
        return [], []
    selected = selected_only and any(p.select for p in mesh.polygons)
    verts: list[tuple[float, float, float]] = []
    tris, remap = [], {}
    for tri in triangles:
        if selected and not mesh.polygons[tri.polygon_index].select:
            continue
        corners = []
        for vi in tri.vertices:
            if vi not in remap:
                remap[vi] = len(verts)
                co = mesh.vertices[vi].co
                verts.append((float(co[0]), float(co[1]), float(co[2])))
            corners.append(remap[vi])
        if len(set(corners)) == 3:
            tris.append(tuple(corners))
    return verts, tris


def object_hull(obj: Any) -> tuple[list, list]:
    # convex hull of the mesh's selected vertices, as a triangle soup
    import bmesh

    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "vertices"):
        return [], []
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        chosen = [v for v in bm.verts if v.select]
        if len(chosen) < 4:
            return [], []
        result = bmesh.ops.convex_hull(
            bm,
            input=chosen,  # type: ignore[arg-type]  # bmesh.ops wants BMVert|BMEdge|BMFace; verts are fine at runtime
            use_existing_faces=False,
        )
        faces = [g for g in result["geom"] if isinstance(g, bmesh.types.BMFace)]
        if not faces:
            return [], []
        verts: list[tuple[float, float, float]] = []
        tris, remap = [], {}
        for face in faces:
            ids = []
            for v in face.verts:
                if v.index not in remap:
                    remap[v.index] = len(verts)
                    verts.append((float(v.co[0]), float(v.co[1]), float(v.co[2])))
                ids.append(remap[v.index])
            for i in range(1, len(ids) - 1):
                if len({ids[0], ids[i], ids[i + 1]}) == 3:
                    tris.append((ids[0], ids[i], ids[i + 1]))
        return verts, tris
    finally:
        bm.free()


def _mesh_for(shape: Any, root: bool = False) -> bpy.types.Mesh:
    kind = shape.kind
    if kind in ("mshs", "mesh") and shape.mesh is not None:
        return _mesh_from(shape.mesh)
    if kind in ("caps", "capsule"):
        verts, faces = _capsule(shape.radius, shape.height, along_x=root)
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
    kind: str,
    radius: float | None = None,
    height: float | None = None,
    size: Any = None,
    root: bool = False,
) -> bpy.types.Mesh:
    if kind in ("caps", "capsule"):
        verts, faces = _capsule(radius, height, along_x=root)
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


# one collection per imported model, tagged with the owner so export finds it
def model_collection(owner: str) -> Any:
    owner = str(owner or "")
    if not owner:
        return None
    for collection in bpy.data.collections:
        if str(collection.get(MODEL_TAG) or "") == owner:
            return collection
    return None


def collection_for(owner: str, context: Any = None) -> Any:
    owner = str(owner or "")
    if owner:
        for collection in bpy.data.collections:
            if str(collection.get(COLLECTION_TAG) or "") == owner:
                return collection
    if context is None:
        return None
    parent = (
        model_collection(owner)
        or getattr(context, "collection", None)
        or getattr(getattr(context, "scene", None), "collection", None)
    )
    if parent is None:
        return None
    stem = Path(owner).stem if owner else ""
    collection = bpy.data.collections.new(f"{stem} {COLLECTION}" if stem else COLLECTION)
    if owner:
        collection[COLLECTION_TAG] = owner
    parent.children.link(collection)
    return collection


def _model_space(bone: Any) -> bool:
    # the root chain keeps model space; the rest of the skeleton is bone-local
    return bone is not None and (bone.parent is None or str(bone.name).lower() == "root")


def _bone_parent_inverse(bone: Any) -> Matrix:
    if _model_space(bone):
        # the root's file values are model-space, as they always were
        tail = Matrix.Translation((0.0, bone.length, 0.0))
        parent = bone.matrix_local @ tail
        return parent.inverted_safe() @ Matrix.Translation(bone.head_local)
    # child bones keep the basis in the game's node space
    return Matrix.Translation((0.0, -bone.length, 0.0)) @ NODE_TO_BONE


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
    collection = collection_for(owner, context)
    if collection is None:
        return []
    made = []
    for cobj in reader.objects:
        for shape in cobj.shapes:
            bone_name = _match_bone(bones, shape.bone)
            parent = bones.get(bone_name) if bone_name else None
            bone = parent.data.bones.get(bone_name) if parent is not None else None
            root = _model_space(bone)
            mesh = _mesh_for(shape, root)
            obj = bpy.data.objects.new(shape.name or f"collision_{shape.type_id}", mesh)
            collection.objects.link(obj)
            _dress(obj)

            # the file's transform is the object's own basis for every kind.
            # A mesh shape's vertices are centred on its own origin (a 12x16 m
            # collision mesh sits at +-0.1 m), so POSITION is what puts it on
            # the bone: reading the object back gives the file's own values,
            # where forcing the location to zero rewrote POSITION on export.
            obj.location = tuple(shape.position or (0.0, 0.0, 0.0))[:3]
            rot = tuple(shape.rotation or (0.0, 0.0, 0.0, 1.0))
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = (rot[3], rot[0], rot[1], rot[2])

            if parent is not None:
                obj.parent = parent
                obj.parent_type = "BONE"
                obj.parent_bone = bone_name
                # keep the rig link (so the proxy follows animation) while the
                # object's own basis stays the file's
                if bone is not None:
                    obj.matrix_parent_inverse = _bone_parent_inverse(bone)

            obj["thedas_io_phy_path"] = ",".join(str(i) for i in shape.path)
            obj["thedas_io_phy_kind"] = shape.kind
            obj["thedas_io_phy_name"] = shape.name or ""
            obj["thedas_io_phy_bone"] = bone_name or ""
            obj[PROXY_FRAME] = PROXY_FRAME_VERSION
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


def add_proxy_for(
    context: Any, spec: dict, arm_obj: Any, bone: Any, owner: str = ""
) -> bpy.types.Object:
    kind = str(spec.get("kind") or "capsule")
    root = _model_space(bone)
    if kind in ("mesh", "mshs") and spec.get("verts") and spec.get("tris"):
        mesh = _mesh_from_geometry(spec["verts"], spec["tris"])
    else:
        mesh = _mesh_for_kind(
            kind, spec.get("radius"), spec.get("height"), spec.get("size"), root=root
        )
    name = str(spec.get("name") or default_name(kind, spec.get("bone") or ""))
    obj = bpy.data.objects.new(name, mesh)
    collection = collection_for(owner, context)
    if collection is None:
        return obj
    collection.objects.link(obj)
    _dress(obj)
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
    obj["thedas_io_phy_kind"] = {"capsule": "caps", "mesh": "mshs"}.get(kind, kind)
    obj["thedas_io_phy_name"] = name
    obj["thedas_io_phy_bone"] = spec.get("bone") or ""
    obj[PROXY_FRAME] = PROXY_FRAME_VERSION
    if kind in ("capsule", "sphere"):
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
    # runs along Z, except on a model-space root bone, where it runs along X.
    kind = obj.get("thedas_io_phy_kind")
    root = False
    parent = getattr(obj, "parent", None)
    if parent is not None and getattr(parent, "type", "") == "ARMATURE":
        bone = parent.data.bones.get(str(getattr(obj, "parent_bone", "") or ""))
        root = _model_space(bone)
    sx, sy, sz = scale
    radius = obj.get("thedas_io_phy_radius")
    if radius is not None:
        across = (sy + sz) / 2.0 if root else (sx + sy) / 2.0
        out["radius"] = float(radius) * across if kind in ("caps",) else float(radius) * sx
    height = obj.get("thedas_io_phy_height")
    if height is not None and kind in ("caps",):
        out["height"] = float(height) * (sx if root else sz)
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


def node_transform(obj: Any, arm_obj: Any, bone: Any) -> tuple:
    # object's world placement as the position/rotation/scale a .phy stores
    m = arm_obj.matrix_world.inverted() @ obj.matrix_world
    if _model_space(bone):
        # root bones keep model space
        offset = m.translation - bone.head_local
        q = m.to_quaternion()
        return (
            (offset[0], offset[1], offset[2]),
            (q.x, q.y, q.z, q.w),
            m.to_scale(),
        )
    rest = bone.matrix_local.to_quaternion()
    q_node = QP_INV @ rest.inverted() @ m.to_quaternion()
    offset = rest.inverted() @ (m.translation - bone.head_local)
    return (
        (offset[1], offset[2], offset[0]),
        (q_node.x, q_node.y, q_node.z, q_node.w),
        m.to_scale(),
    )


def _adopted_spec(obj: Any, arm_obj: Any, bone_name: str) -> dict:
    from mathutils import Vector

    bone = arm_obj.data.bones[bone_name]
    arm_inv = arm_obj.matrix_world.inverted()
    pos, rot, _scale = node_transform(obj, arm_obj, bone)

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
        "rotation": rot,
        "name": default_name(kind, bone_name),
    }
    if kind == "box":
        # an armature-aligned box covering the mesh in its current pose
        if hasattr(obj.data, "bound_box"):
            rest = bone.matrix_local.to_quaternion()
            corners = [arm_inv @ (obj.matrix_world @ Vector(c)) for c in obj.data.bound_box]
            lo = [min(c[i] for c in corners) for i in range(3)]
            hi = [max(c[i] for c in corners) for i in range(3)]
            dims = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            centre = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
            if _model_space(bone):
                spec["box_dim"] = (dims[0], dims[1], dims[2])
                offset = centre - bone.head_local
                spec["position"] = (offset[0], offset[1], offset[2])
                spec["rotation"] = (0.0, 0.0, 0.0, 1.0)
            else:
                offset = rest.inverted() @ (centre - bone.head_local)
                spec["box_dim"] = (dims[1], dims[2], dims[0])
                spec["position"] = (offset[1], offset[2], offset[0])
                axis = QP_INV @ rest.inverted()
                spec["rotation"] = (axis.x, axis.y, axis.z, axis.w)
    else:
        spec["radius"] = float(obj.get("thedas_io_phy_radius") or 0.05)
        if kind == "capsule":
            spec["height"] = float(obj.get("thedas_io_phy_height") or 0.0)
    return spec


def adopted_specs(item: Any) -> list:
    arm = item.get("armature")
    if arm is None:
        return []
    owner = item.get("file", "")
    collection = collection_for(owner)
    if collection is None:
        return []
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


def _upgrade_proxy(obj: Any, arm: Any) -> None:
    if obj.get(PROXY_FRAME) == PROXY_FRAME_VERSION:
        return
    bone = None
    if arm is not None:
        name = str(obj.get("thedas_io_phy_bone") or "")
        bone = arm.data.bones.get(name) if name else None
    if bone is not None:
        if obj.get("thedas_io_phy_new") and bone.parent is not None:
            # old added shapes on a child bone stored their basis in armature space
            rest = bone.matrix_local.to_quaternion()
            offset = rest.inverted() @ Vector(obj.location)
            obj.location = (offset[1], offset[2], offset[0])
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = QP_INV @ rest.inverted() @ obj.rotation_quaternion
        obj.matrix_parent_inverse = _bone_parent_inverse(bone)
    if str(obj.get("thedas_io_phy_kind") or "") in ("caps", "capsule"):
        old = obj.data
        obj.data = _mesh_for_kind(
            "capsule",
            obj.get("thedas_io_phy_radius"),
            obj.get("thedas_io_phy_height"),
            root=_model_space(bone),
        )
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
    obj[PROXY_FRAME] = PROXY_FRAME_VERSION


def migrate_item(item: Any, context: Any = None) -> list:
    # old blends: proxies in the global collection, armature-space bases
    owner = str(item.get("file") or "")
    if not owner:
        return []
    arm = item.get("armature")
    scene = getattr(context, "scene", None)
    model = model_collection(owner)
    if model is None:
        parent = getattr(context, "collection", None) or getattr(scene, "collection", None)
        if parent is not None:
            model = bpy.data.collections.new(Path(owner).stem)
            model[MODEL_TAG] = owner
            parent.children.link(model)
            for obj in [arm, *(item.get("meshes") or [])]:
                if obj is None:
                    continue
                for old in list(getattr(obj, "users_collection", ()) or ()):
                    old.objects.unlink(obj)
                model.objects.link(obj)
    collection = collection_for(owner, context)
    proxies = []
    for obj in bpy.data.objects:
        if str(obj.get("thedas_io_phy_owner") or "") != owner:
            continue
        if not obj.get("thedas_io_phy_kind"):
            continue
        _upgrade_proxy(obj, arm)
        if collection is not None:
            for old in list(getattr(obj, "users_collection", ()) or ()):
                if old is not collection:
                    old.objects.unlink(obj)
            if collection.name not in {c.name for c in obj.users_collection}:
                collection.objects.link(obj)
        proxies.append(obj)
    if proxies:
        item["collision"] = proxies
    return proxies
