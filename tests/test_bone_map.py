"""Bone-order lookup tests (no Blender process)."""

from __future__ import annotations

from types import SimpleNamespace

from thedas_io import bone_map


class Bone(dict):
    def __init__(self, name: str, global_id: int) -> None:
        super().__init__({bone_map.BONE_GLOBAL: global_id})
        self.name = name


class Bones(dict):
    def __init__(self, *bones: Bone) -> None:
        super().__init__((bone.name, bone) for bone in bones)

    def __iter__(self):
        return iter(self.values())


class Obj:
    type = "MESH"

    def __init__(self, order: list[int], arm=None) -> None:
        self.props: dict = {}
        if order:
            self.props[bone_map.BONE_ORDER] = order
        self.modifiers = [SimpleNamespace(type="ARMATURE", object=arm)] if arm else []
        self.vertex_groups: list = []

    def get(self, key, default=None):
        return self.props.get(key, default)

    def __setitem__(self, key, value):
        self.props[key] = value


class Arm:
    def __init__(self) -> None:
        self.props: dict = {}
        self.pose = SimpleNamespace(bones=Bones(Bone("A", 11), Bone("B", 22), Bone("C", 33)))

    def get(self, key, default=None):
        return self.props.get(key, default)

    def __setitem__(self, key, value):
        self.props[key] = value


def _arm() -> Arm:
    return Arm()


def _ordered_arm() -> Arm:
    arm = Arm()
    arm[bone_map.BONE_ORDER] = [11, 22, 33]
    return arm


def test_local_for_global():
    assert bone_map.local_for_global([11, 22, 33], 22) == 1
    assert bone_map.local_for_global([11], 33) is None
    assert bone_map.local_for_global([11], None) is None


def test_object_order_wins_and_labels_resolve():
    arm = _arm()
    obj = Obj([11, 33], arm)
    assert bone_map.order_of(obj, arm) == [11, 33]
    assert bone_map.index_for_group(obj, "A") == 0
    assert bone_map.index_for_group(obj, "C") == 1
    assert bone_map.index_for_group(obj, "B") is None
    assert bone_map.bone_labels(obj) == {"A": 0, "C": 1}
    assert bone_map.bone_for_index(obj, arm, 1) == "C"


def test_armature_order_is_the_fallback():
    arm = _ordered_arm()
    obj = Obj([], arm)
    assert bone_map.order_of(obj, arm) == [11, 22, 33]
    assert bone_map.index_for_group(obj, "B") == 1


def test_bone_entries_include_unindexed_bones():
    arm = _arm()
    obj = Obj([11, 33], arm)
    assert bone_map.bone_entries(obj) == [("A", 0), ("B", None), ("C", 1)]


def test_pick_menu_lists_every_bone_and_filters():
    arm = _arm()
    obj = Obj([11, 33], arm)
    op = bone_map.THEDAS_IO_OT_pick_group_bone()
    op.search = ""
    scene = SimpleNamespace(thedas_io_allow_unindexed_bones=False)
    context = SimpleNamespace(object=obj, scene=scene)
    assert [item[0] for item in bone_map._bone_items(op, context)] == [
        bone_map._UNMAPPED,
        "A",
        "C",
    ]
    scene.thedas_io_allow_unindexed_bones = True
    assert [item[0] for item in bone_map._bone_items(op, context)] == [
        bone_map._UNMAPPED,
        "A",
        "B",
        "C",
    ]
    op.search = "b"
    assert [item[0] for item in bone_map._bone_items(op, context)] == [
        bone_map._UNMAPPED,
        "B",
    ]


def test_manual_index_override_writes_any_bone():
    arm = _arm()
    obj = Obj([11, 33], arm)
    op = bone_map.THEDAS_IO_OT_pick_group_bone()
    op.group = "custom"
    op.bone = "B"
    op.index = 7
    scene = SimpleNamespace(thedas_io_allow_unindexed_bones=True)
    assert op.execute(SimpleNamespace(object=obj, scene=scene)) == {"FINISHED"}
    assert bone_map.current_index(obj, "custom") == 7


def test_mapping_write_and_clear():
    obj = Obj([11], _arm())
    assert bone_map.current_index(obj, "A") is None
    bone_map.set_bone_index(obj, "A", 0)
    assert bone_map.current_index(obj, "A") == 0
    bone_map.set_bone_index(obj, "A", None)
    assert bone_map.current_index(obj, "A") is None


def test_auto_map_operator_matches_names():
    arm = _arm()
    obj = Obj([11, 22, 33], arm)
    obj.vertex_groups = [
        SimpleNamespace(name="A"),
        SimpleNamespace(name="B"),
        SimpleNamespace(name="nope"),
    ]
    op = bone_map.THEDAS_IO_OT_auto_map_bones()
    result = op.execute(SimpleNamespace(object=obj))
    assert result == {"FINISHED"}
    assert bone_map.current_index(obj, "A") == 0
    assert bone_map.current_index(obj, "B") == 1
    assert bone_map.current_index(obj, "nope") is None
