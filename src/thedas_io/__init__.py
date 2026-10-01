from __future__ import annotations

import os
import sys


def _libs():
    path = os.path.join(os.path.dirname(__file__), "libs")
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


_libs()


def register():
    _libs()
    from .ui import register as _register

    return _register()


def unregister():
    from .ui import unregister as _unregister

    return _unregister()


__all__ = ["register", "unregister"]
