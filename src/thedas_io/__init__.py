from __future__ import annotations


def register() -> None:
    from .ui import register as _register

    return _register()


def unregister() -> None:
    from .ui import unregister as _unregister

    return _unregister()


__all__ = ["register", "unregister"]
