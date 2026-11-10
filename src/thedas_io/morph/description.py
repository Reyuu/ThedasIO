# character presets: a .mor lists the parts a preset is built from, its tint
# files and, per mesh node, the target vertex positions plus the material
# inputs it overrides; a .mop carries only the overrides. da2 names its nodes
# by a u32 id, so they arrive as the raw id text, the same as .ani channels.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..gff40.description import GFFReader

L_NAME = 2
L_POSITION = 4
L_VECTOR4_LIST = 17
L_VERTEX_COUNT = 8013
L_VERTEX_DATA = 8024
L_PARTS = 23000
L_TINTS = 23001
L_NODES = 23002
L_TEXTURE_NAME = 23003
L_TEXTURE_PARAM = 23004
L_VECTOR_PARAM = 23005
L_FLOAT_PARAM = 23006
L_FLOAT_VALUE = 23007
L_MORPH_NAME = 23008
L_MAT_NODE = 23009
L_MAT_PARAM = 23010
L_MAT_INDEX = 23011
L_MAT_VALUE = 23012
L_MAT_VECTOR = 23013
L_MAT_PARAMS = 23014
L_MAT_VEC_PARAMS = 23015
L_MODEL_NAME = 23016
L_MODEL_VALUE = 23017
L_MODEL_PARAMS = 23018
L_TEX_NODE = 23019
L_TEX_PARAM = 23020
L_TEX_NAME = 23021
L_TEXTURES = 23022

# four floats a vertex: xyz plus a w every shipped file leaves at 1.0
VERTEX_STRIDE = 4


@dataclass
class MorphPart:
    name: str
    index: int


@dataclass
class MorphTint:
    name: str
    index: int


@dataclass
class MorphTexture:
    name: str
    param: str = ""
    node: str = ""


@dataclass
class MorphVectorParam:
    param: str
    values: list = field(default_factory=list)
    node: str = ""


@dataclass
class MorphFloatParam:
    param: str
    value: float = 0.0
    node: str = ""


@dataclass
class MorphTarget:
    count: int = 0
    vertices: list = field(default_factory=list)


@dataclass
class MorphNode:
    name: str = ""
    position: tuple = ()
    target: MorphTarget = field(default_factory=MorphTarget)
    textures: list = field(default_factory=list)
    vectors: list = field(default_factory=list)
    floats: list = field(default_factory=list)


@dataclass
class MorphMaterialParam:
    node: str
    param: str
    index: int
    value: float


@dataclass
class MorphMaterialVector:
    node: str
    param: str
    index: int
    value: tuple = ()


@dataclass
class MorphModelParam:
    model: str
    value: float


def _text(value: Any) -> str:
    # da2 node names are u32 ids, kept as the decimal text callers already use
    return "" if value is None else str(value)


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _vector(value: Any) -> tuple:
    return tuple(float(v) for v in value) if isinstance(value, (list, tuple)) else ()


def _target(node: dict, name: str) -> MorphTarget:
    count = int(node.get(L_VERTEX_COUNT) or 0)
    data = _list(node.get(L_VERTEX_DATA))
    if count <= 0:
        return MorphTarget(0, [])
    if len(data) != count * VERTEX_STRIDE:
        raise ValueError(
            f"node {name!r}: {count} vertices need {count * VERTEX_STRIDE} floats, "
            f"file has {len(data)}"
        )
    return MorphTarget(
        count, [tuple(data[i : i + VERTEX_STRIDE]) for i in range(0, len(data), VERTEX_STRIDE)]
    )


def _morph_node(node: dict) -> MorphNode:
    name = _text(node.get(L_NAME))
    return MorphNode(
        name=name,
        position=_vector(node.get(L_POSITION)),
        target=_target(node, name),
        textures=[
            MorphTexture(
                name=_text(tex.get(L_TEXTURE_NAME)), param=_text(tex.get(L_NAME)), node=name
            )
            for tex in _list(node.get(L_TEXTURE_PARAM))
        ],
        vectors=[
            MorphVectorParam(
                param=_text(vec.get(L_NAME)),
                values=[_vector(v) for v in _list(vec.get(L_VECTOR4_LIST))],
                node=name,
            )
            for vec in _list(node.get(L_VECTOR_PARAM))
        ],
        floats=[
            MorphFloatParam(
                param=_text(flt.get(L_NAME)), value=_float(flt.get(L_FLOAT_VALUE)), node=name
            )
            for flt in _list(node.get(L_FLOAT_PARAM))
        ],
    )


def _parts(values: Any) -> list:
    # a slot can be empty (da2 writes a null), so keep the index for every one
    return [MorphPart(_text(name), i) for i, name in enumerate(_list(values))]


def _tints(values: Any) -> list:
    return [MorphTint(_text(name), i) for i, name in enumerate(_list(values))]


class MORReader:
    # MORP: the preset itself, its parts, tints and per-node targets
    def __init__(self, raw: bytes, gff: Any = None):
        gff = gff if gff is not None else GFFReader(raw)
        if gff.file_type != b"MORP":
            raise ValueError(f"not a MOR file (GFF type {gff.file_type!r})")
        self.gff = gff
        self.version = gff.version
        root = gff.get_from_data_block(0, 0)
        self.name = _text(root.get(L_NAME))
        self.parts = _parts(root.get(L_PARTS))
        self.tints = _tints(root.get(L_TINTS))
        self.nodes = [_morph_node(node) for node in _list(root.get(L_NODES))]

    def summary(self) -> dict:
        return {
            "name": self.name,
            "parts": len(self.parts),
            "tints": len(self.tints),
            "nodes": len(self.nodes),
            "vertices": sum(node.target.count for node in self.nodes),
        }


class MOPReader:
    # MOP : overrides applied on top of a preset's own values
    def __init__(self, raw: bytes, gff: Any = None):
        gff = gff if gff is not None else GFFReader(raw)
        if gff.file_type != b"MOP ":
            raise ValueError(f"not a MOP file (GFF type {gff.file_type!r})")
        self.gff = gff
        self.version = gff.version
        root = gff.get_from_data_block(0, 0)
        self.name = _text(root.get(L_MORPH_NAME))
        self.parts = _parts(root.get(L_PARTS))
        self.tints = _tints(root.get(L_TINTS))
        self.material = [
            MorphMaterialParam(
                node=_text(cell.get(L_MAT_NODE)),
                param=_text(cell.get(L_MAT_PARAM)),
                index=int(cell.get(L_MAT_INDEX) or 0),
                value=_float(cell.get(L_MAT_VALUE)),
            )
            for cell in _list(root.get(L_MAT_PARAMS))
        ]
        self.material_vectors = [
            MorphMaterialVector(
                node=_text(cell.get(L_MAT_NODE)),
                param=_text(cell.get(L_MAT_PARAM)),
                index=int(cell.get(L_MAT_INDEX) or 0),
                value=_vector(cell.get(L_MAT_VECTOR)),
            )
            for cell in _list(root.get(L_MAT_VEC_PARAMS))
        ]
        self.model = [
            MorphModelParam(
                model=_text(cell.get(L_MODEL_NAME)), value=_float(cell.get(L_MODEL_VALUE))
            )
            for cell in _list(root.get(L_MODEL_PARAMS))
        ]
        self.textures = [
            MorphTexture(
                node=_text(cell.get(L_TEX_NODE)),
                param=_text(cell.get(L_TEX_PARAM)),
                name=_text(cell.get(L_TEX_NAME)),
            )
            for cell in _list(root.get(L_TEXTURES))
        ]

    def summary(self) -> dict:
        return {
            "name": self.name,
            "parts": len(self.parts),
            "tints": len(self.tints),
            "material": len(self.material),
            "vectors": len(self.material_vectors),
            "model": len(self.model),
            "textures": len(self.textures),
        }


def read(raw: bytes) -> MORReader | MOPReader:
    # the gff file type says which of the two it is
    gff = GFFReader(raw)
    if gff.file_type == b"MORP":
        return MORReader(raw, gff)
    if gff.file_type == b"MOP ":
        return MOPReader(raw, gff)
    raise ValueError(f"not a MOR or MOP file (GFF type {gff.file_type!r})")


def load(path: Any) -> MORReader | MOPReader:
    return read(Path(path).read_bytes())
