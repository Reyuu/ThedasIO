from __future__ import annotations


def register():
    from .ui import register as _register

    return _register()


def unregister():
    from .ui import unregister as _unregister

    return _unregister()


__all__ = ["register", "unregister"]
