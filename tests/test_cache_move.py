# The cache must survive a cross-drive move (temp C: -> .blend on F:).

from __future__ import annotations

import sys
from pathlib import Path

import fake_bpy

sys.modules["bpy"] = fake_bpy.bpy

from thedas_io.ui import _move_file


def test_move_file_copies_when_rename_cannot_cross_drives(tmp_path, monkeypatch):
    # os.replace refuses cross-volume moves on Windows; fall back to copy.
    src = tmp_path / "a.dds"
    dest = tmp_path / "b.dds"
    src.write_bytes(b"pixels")

    def refuse(self, target):
        raise OSError(17, "Invalid cross-device link")

    monkeypatch.setattr(Path, "replace", refuse)
    _move_file(src, dest)
    assert dest.read_bytes() == b"pixels"
    assert not src.exists()


def test_move_file_uses_rename_when_it_works(tmp_path):
    src = tmp_path / "a.dds"
    dest = tmp_path / "b.dds"
    src.write_bytes(b"pixels")
    _move_file(src, dest)
    assert dest.read_bytes() == b"pixels"
    assert not src.exists()
