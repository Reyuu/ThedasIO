from __future__ import annotations

import os

import pytest

from thedas_io.fsio import save_image_atomic, write_bytes_atomic, write_text_atomic


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


def test_an_encoder_that_dies_partway_keeps_the_old_png(tmp_path):
    # the reason images go via a buffer: this fake writes half a file to the
    # real path and then gives up, which is what a killed PIL encode looks like
    target = tmp_path / "base.png"
    write_bytes_atomic(target, b"previous png bytes")

    class HalfWriter:
        def save(self, handle, _fmt):
            # real PIL opens whatever it is handed, so this is exactly how a
            # killed encode leaves a half file at the destination
            should_close = False
            if not hasattr(handle, "write"):
                handle = open(handle, "wb")  # noqa: SIM115 - faking PIL
                should_close = True
            handle.write(b"\x89PNG truncated")
            if should_close:
                handle.close()
            raise OSError("encoder died")

    with pytest.raises(OSError, match="encoder died"):
        save_image_atomic(HalfWriter(), target, "PNG")

    assert target.read_bytes() == b"previous png bytes"
    assert [p.name for p in tmp_path.iterdir()] == ["base.png"]


def test_an_image_lands_intact(tmp_path):
    from PIL import Image

    target = tmp_path / "map.png"
    save_image_atomic(Image.new("RGBA", (4, 4), (10, 20, 30, 255)), target, "PNG")
    with Image.open(target) as im:
        assert im.size == (4, 4)
        assert im.convert("RGBA").getpixel((0, 0)) == (10, 20, 30, 255)
    assert [p.name for p in tmp_path.iterdir()] == ["map.png"]


def test_a_plain_permission_error_is_not_retried(tmp_path, monkeypatch):
    # a locked/denied destination is not transient for us; retrying it hides the
    # real error and used to leave .tmp.1/.2/.3 files behind
    target = tmp_path / "a.msh"
    calls = []

    def denied(*args, **kwargs):
        calls.append(1)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        write_bytes_atomic(target, b"x")
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_windows_sharing_violation_is_retried_then_succeeds(tmp_path, monkeypatch):
    target = tmp_path / "a.msh"
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) == 1:
            exc = PermissionError(13, "in use")
            exc.winerror = 32  # ERROR_SHARING_VIOLATION
            raise exc
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    assert write_bytes_atomic(target, b"x") == target
    assert len(calls) == 2
    assert target.read_bytes() == b"x"
    assert [p.name for p in tmp_path.iterdir()] == ["a.msh"]


def test_a_sharing_violation_that_never_clears_raises_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "a.msh"
    calls = []

    def locked(*args, **kwargs):
        calls.append(1)
        exc = PermissionError(13, "in use")
        exc.winerror = 32
        raise exc

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError) as excinfo:
        write_bytes_atomic(target, b"x", attempts=3, delay=0)
    assert len(calls) == 3
    assert excinfo.value.winerror == 32
    assert list(tmp_path.iterdir()) == []
