from __future__ import annotations

import os

import pytest

from thedas_io.fsio import write_bytes_atomic, write_text_atomic


def test_a_finished_write_replaces_the_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "a.msh"
    write_bytes_atomic(target, b"first")
    assert target.read_bytes() == b"first"

    write_bytes_atomic(target, b"second")
    assert target.read_bytes() == b"second"
    assert [p.name for p in tmp_path.iterdir()] == ["a.msh"]


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    # this is the whole point: a full disk or a Ctrl-C must not leave a
    # truncated file where the user's working override was
    target = tmp_path / "a.dds"
    write_bytes_atomic(target, b"good bytes")

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="No space left"):
        write_bytes_atomic(target, b"half")

    assert target.read_bytes() == b"good bytes"
    assert [p.name for p in tmp_path.iterdir()] == ["a.dds"]


def test_a_write_that_fails_partway_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "a.msh"
    write_bytes_atomic(target, b"good")

    class Short:
        def __init__(self, fh):
            self.fh = fh

        def write(self, _data):
            self.fh.write(b"x")
            raise OSError("disk full")

        def flush(self):
            pass

        def fileno(self):
            return self.fh.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

    real_open = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: Short(real_open(*a, **k)))
    with pytest.raises(OSError, match="disk full"):
        write_bytes_atomic(target, b"half")

    assert target.read_bytes() == b"good"
    assert [p.name for p in tmp_path.iterdir()] == ["a.msh"]


def test_text_writes_are_atomic_too(tmp_path):
    target = tmp_path / "model.mtl"
    write_text_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert [p.name for p in tmp_path.iterdir()] == ["model.mtl"]