# Headless stand-in for bpy: only what the extension touches.

from __future__ import annotations

import os
from types import ModuleType, SimpleNamespace


class FakeLayout:
    def __init__(self):
        self.labels = []
        self.props = []
        self.lists = []
        self.operators = []

    def label(self, text=""):
        self.labels.append(text)

    def prop(self, data, prop, text="", expand=False):
        # real layout.prop() returns None, so branches must read the data and
        # never this return; the fake matches it to keep the tests honest
        self.props.append((data, prop, text))

    def template_list(
        self, listtype, list_id, dataptr, propname, active_dataptr, active_propname, rows=5
    ):
        self.lists.append((listtype, propname))

    def box(self):
        return self

    def split(self, factor=0.5, align=False):
        return self

    def operator(self, opname, text=""):
        self.operators.append(opname)
        return SimpleNamespace()


class Panel:
    def __init__(self):
        self.layout = FakeLayout()


class Operator:
    def __init__(self):
        self.reports = []

    def report(self, level, msg):
        self.reports.append((set(level), msg))


class PropertyGroup:
    pass


class UIList:
    pass


class FakeCollection(list):
    def add(self):
        item = SimpleNamespace()
        self.append(item)
        return item

    def remove(self, index):
        # mirrors CollectionProperty.remove(index), not list.remove(value)
        self.pop(index)


class _PropDef:
    # What a bpy.props.* factory was called with; the fake keeps kwargs.

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Props:
    @staticmethod
    def StringProperty(**kwargs):
        return _PropDef(**kwargs)

    @staticmethod
    def IntProperty(**kwargs):
        return _PropDef(**kwargs)

    @staticmethod
    def BoolProperty(**kwargs):
        return _PropDef(**kwargs)

    @staticmethod
    def CollectionProperty(**kwargs):
        return _PropDef(**kwargs)

    @staticmethod
    def EnumProperty(**kwargs):
        return _PropDef(**kwargs)


class _Types:
    Panel = Panel
    Operator = Operator
    PropertyGroup = PropertyGroup
    UIList = UIList
    AddonPreferences = Panel
    Scene = SimpleNamespace()


class _Path:
    @staticmethod
    def abspath(path):
        return os.path.abspath(path) if path else path


class _Utils:
    def __init__(self):
        self.registered = []

    def register_class(self, cls):
        self.registered.append(cls)

    def unregister_class(self, cls):
        self.registered.remove(cls)


class _Handlers:
    def __init__(self):
        self.load_post = []
        self.depsgraph_update_post = []
        self.save_pre = []
        self.save_post = []

    @staticmethod
    def persistent(func):
        return func


class _App:
    def __init__(self):
        self.handlers = _Handlers()


class FakePixels:
    def __init__(self):
        self.writes = []

    def foreach_set(self, flat):
        self.writes.append(list(flat))


class FakeImage:
    def __init__(self, name, width, height):
        self.name = name
        self.size = [width, height]
        self.pixels = FakePixels()
        self.alpha_mode = ""
        self.colorspace_settings = SimpleNamespace(name="")
        self._props = {}

    def update(self):
        pass

    def pack(self):
        pass

    def __setitem__(self, key, value):
        self._props[key] = value

    def __getitem__(self, key):
        return self._props[key]

    def get(self, key, default=None):
        return self._props.get(key, default)


class FakeImages(list):
    def new(self, name, width, height, alpha=False):
        image = FakeImage(name, width, height)
        self.append(image)
        return image

    def get(self, name):
        for image in self:
            if image.name == name:
                return image
        return None

    def remove(self, image):
        list.remove(self, image)


class _Data:
    def __init__(self):
        self.scenes = []
        self.images = FakeImages()


class _Bpy(ModuleType):
    def __init__(self):
        super().__init__("bpy")
        self.types = _Types()
        self.utils = _Utils()
        self.props = _Props()
        self.path = _Path()
        self.app = _App()
        self.data = _Data()


bpy = _Bpy()


class _Matrix:
    def __init__(self, *args):
        self.args = args

    @staticmethod
    def Identity(size):
        return _Matrix()

    @staticmethod
    def Translation(vec):
        return _Matrix()


class _Quaternion:
    def __init__(self, *args):
        self.args = args


class _Mathutils(ModuleType):
    def __init__(self):
        super().__init__("mathutils")
        self.Matrix = _Matrix
        self.Quaternion = _Quaternion


mathutils = _Mathutils()
