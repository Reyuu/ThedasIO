# A PHY is a GFF whose root names the model hierarchy it belongs to, then repeats
# the MMH's node tree with a ``cobj`` (collision object) hung off the nodes that
# carry collision::
# 
#     mdlh   GFF_MMH_NAME = 'qm_uhm_basb_0.mmh'
#       node GOB > GOD > Root > ... > Neck1
#         cobj    density, obj type, collision group
#           shap  TYPE, POSITION, ROTATION, NAME, COLLISION_MASK_*, VOLUME
#             TYPE_STRUCT -> generic ref to sph / caps / box / mshs / cvx


from __future__ import annotations

import struct
from pathlib import Path

from ..gff40.description import GFFReader
from ..gff40.labels import DATA as LABELS
from . import nxs

_ID = {name: value for value, name in LABELS.items()}

L_NAME = _ID["GFF_MMH_NAME"]
L_CHILDREN = _ID["GFF_MMH_CHILDREN"]
L_SHAPE_TYPE = _ID["GFF_MMH_SHAPE_TYPE"]
L_SHAPE_NAME = _ID["GFF_MMH_SHAPE_NAME"]
L_SHAPE_POS = _ID["GFF_MMH_SHAPE_POSITION"]
L_SHAPE_ROT = _ID["GFF_MMH_SHAPE_ROTATION"]
L_SHAPE_VOLUME = _ID["GFF_MMH_SHAPE_VOLUME"]
L_SHAPE_TYPESTRUCT = _ID["GFF_MMH_SHAPE_TYPE_STRUCT"]
L_COBJ_DENSITY = _ID["GFF_MMH_NODE_COLLISION_OBJ_DENSITY"]
L_COBJ_TYPE = _ID["GFF_MMH_NODE_COLLISION_OBJ_TYPE"]
L_COBJ_GROUP = _ID["GFF_MMH_COLLISION_GROUP"]

# the collision masks the toolset tutorial tells modders to flip
MASK_LABELS = (
    "GFF_MMH_SHAPE_COLLISION_MASK_ALL",
    "GFF_MMH_SHAPE_COLLISION_MASK_NONE",
    "GFF_MMH_SHAPE_COLLISION_MASK_ITEMS",
    "GFF_MMH_SHAPE_COLLISION_MASK_CREATURES",
    "GFF_MMH_SHAPE_COLLISION_MASK_PLACEABLES",
    "GFF_MMH_SHAPE_COLLISION_MASK_TRIGGERS",
    "GFF_MMH_SHAPE_COLLISION_MASK_STATIC_GEOMETRY",
    "GFF_MMH_SHAPE_COLLISION_MASK_NONWALKABLE",
)
MASKS = tuple((_ID[n], n.removeprefix("GFF_MMH_SHAPE_COLLISION_MASK_").lower())
              for n in MASK_LABELS)

# SHAPE_TYPE is an index into the per-type struct the TYPE_STRUCT generic points
# at; the fourcc is the authority, but the id is what the file stores
TYPE_IDS = {0: "sphere", 1: "sphere", 2: "box", 3: "capsule", 4: "convex", 5: "mesh"}

L_RADIUS = _ID["GFF_MMH_SHAPE_RADIUS"]
L_HEIGHT = _ID["GFF_MMH_SHAPE_HEIGHT"]
L_BOX_DIM = _ID["GFF_MMH_SHAPE_BOX_DIM"]
L_COOKED = _ID["GFF_MMH_SHAPE_COOKED_DATA_STREAM"]

TYPE_FIELDS = {
    "sphere": ((L_RADIUS,), ()),
    "capsule": ((L_RADIUS, L_HEIGHT), ()),
    "box": ((L_BOX_DIM,), ()),
}


def _fourcc(value: int) -> str:
    return struct.pack("<I", value).decode("ascii", "replace")


def _mesh(value) -> object:
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return nxs.parse(bytes(value))
    except (ValueError, struct.error):
        return None


class Shape:

    __slots__ = (
        "bone",
        "box_dim",
        "height",
        "kind",
        "masks",
        "mesh",
        "name",
        "path",
        "position",
        "radius",
        "rotation",
        "type_id",
        "volume",
    )

    kind: str
    type_id: int | None
    name: str | None
    bone: str | None
    position: tuple
    rotation: tuple
    volume: float
    radius: float
    height: float
    box_dim: tuple
    masks: dict
    path: tuple
    mesh: object

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))
        self.masks = self.masks or {}

    def __repr__(self):
        return (f"<Shape {self.kind} {self.name!r} on {self.bone!r} "
                f"r={self.radius} h={self.height}>")

    @property
    def editable(self) -> dict:
        """the value set the Blender panel exposes"""
        out: dict = {
            "name": self.name,
            "position": tuple(self.position or (0.0, 0.0, 0.0, 0.0)),
            "rotation": tuple(self.rotation or (0.0, 0.0, 0.0, 1.0)),
        }
        if self.radius is not None:
            out["radius"] = float(self.radius)
        if self.height is not None:
            out["height"] = float(self.height)
        for key, value in self.masks.items():
            out[f"mask_{key}"] = int(value)
        return out


class CollisionObject:

    __slots__ = ("bone", "density", "group", "obj_type", "path", "shapes")

    bone: str | None
    density: float
    group: int
    obj_type: int
    path: tuple
    shapes: list

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))
        self.shapes = self.shapes or []

    def __repr__(self):
        return f"<CollisionObject on {self.bone!r} shapes={len(self.shapes or [])}>"


class PhyReader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.gff = GFFReader(raw)
        self.root = self.gff.get_from_data_block(0, 0)
        self.mmh_name = self.root.get(L_NAME)
        self.objects: list[CollisionObject] = []
        self.orphans: list[str] = []
        self._walk(self.root, None, ())

    # -- tree walk ---------------------------------------------------------
    def _walk(self, node, bone, path):
        # recurse into every child, not just bone nodes: mesh collision hangs off
        # an mshh chunk, capsule collision off the bone. only a 'node' updates
        # which bone we are on.
        #
        # child indices skip NULL refs, matching export._walk's convention, so a
        # path recorded here addresses the same instance on the way back out.
        children = node.get(L_CHILDREN) or []
        i = 0
        for child in children:
            if not isinstance(child, dict) or child.get("value") is None:
                continue
            index = child.get("struct_index")
            kind = (_fourcc(self.gff.struct_defs[index].type_fourcc)
                    if isinstance(index, int) and 0 <= index < self.gff.nstruct
                    else "?")
            values = child.get("value") or {}
            here = path + (i,)
            i += 1
            if kind == "node":
                self._walk(values, values.get(L_NAME), here)
            elif kind == "cobj":
                self.objects.append(self._cobj(values, bone, here))
            else:
                self._walk(values, bone, here)

    def _cobj(self, values, bone, path) -> CollisionObject:
        shapes = []
        children = values.get(L_CHILDREN) or []
        i = 0
        for child in children:
            if not isinstance(child, dict) or child.get("value") is None:
                continue
            shape = child.get("value") or {}
            here = path + (i,)
            i += 1
            if shape.get(L_SHAPE_TYPE) is not None:
                shapes.append(self._shape(shape, bone, here))
        return CollisionObject(
            bone=bone,
            density=values.get(L_COBJ_DENSITY),
            obj_type=values.get(L_COBJ_TYPE),
            group=values.get(L_COBJ_GROUP),
            shapes=shapes,
            path=path,
        )

    def _shape(self, values, bone, path) -> Shape:
        type_id = values.get(L_SHAPE_TYPE)
        kind = TYPE_IDS.get(type_id, f"type{type_id}")
        radius = height = None
        box_dim = None
        mesh = None
        target = values.get(L_SHAPE_TYPESTRUCT)
        if isinstance(target, dict):
            index = target.get("struct_index")
            if isinstance(index, int) and 0 <= index < self.gff.nstruct:
                kind = _fourcc(self.gff.struct_defs[index].type_fourcc)
            inner = target.get("value") or {}
            if isinstance(inner, dict):
                radius = inner.get(L_RADIUS)
                height = inner.get(L_HEIGHT)
                box_dim = inner.get(L_BOX_DIM)
                if kind == "mshs":
                    mesh = _mesh(inner.get(L_COOKED))
        masks = {}
        for label, short in MASKS:
            value = values.get(label)
            if isinstance(value, int):
                masks[short] = value
        return Shape(
            kind=kind,
            type_id=type_id,
            name=values.get(L_SHAPE_NAME),
            bone=bone,
            position=values.get(L_SHAPE_POS),
            rotation=values.get(L_SHAPE_ROT),
            volume=values.get(L_SHAPE_VOLUME),
            radius=radius,
            height=height,
            box_dim=box_dim,
            mesh=mesh,
            masks=masks,
            path=path,
        )

    @property
    def shapes(self) -> list:
        return [s for o in self.objects for s in o.shapes]

    def summary(self) -> dict:
        kinds: dict = {}
        for s in self.shapes:
            kinds[s.kind] = kinds.get(s.kind, 0) + 1
        return {
            "mmh": self.mmh_name,
            "objects": len(self.objects),
            "shapes": len(self.shapes),
            "kinds": kinds,
            "bones": sorted({o.bone for o in self.objects if o.bone}),
        }


def load(path) -> PhyReader:
    return PhyReader(Path(path).read_bytes())