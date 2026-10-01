# Resized textures: live size != source size must export, not skip.

from __future__ import annotations

import io
import sys

import fake_bpy

sys.modules.setdefault("bpy", fake_bpy.bpy)

import numpy as np
from PIL import Image


class _Pixels:
    # Mimics Blender: foreach_get demands exactly w*h*4 floats.

    def __init__(self, rgba: np.ndarray, size):
        self._flat = rgba.astype(np.float32).reshape(-1) / 255.0
        self._size = tuple(size)
        self.set_calls = []

    def foreach_get(self, buf):
        expect = self._size[0] * self._size[1] * 4
        if len(buf) != expect:
            raise ValueError(f"expected sequence size {expect}, got {len(buf)}")
        buf[:] = self._flat[: len(buf)]

    def foreach_set(self, flat):
        self.set_calls.append(len(flat))
        self._flat = np.asarray(flat, dtype=np.float32)


class _LiveImage:
    def __init__(self, name, rgba: np.ndarray):
        h, w, _ = rgba.shape
        self.name = name
        self.size = [w, h]
        self.pixels = _Pixels(rgba, (w, h))
        self.packed = 0
        self.scaled = []

    def scale(self, w, h):
        self.scaled.append((w, h))
        self.size = [w, h]

    def update(self):
        pass

    def pack(self):
        self.packed += 1


def _tga(color, size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "TGA")
    return buf.getvalue()


def test_same_size_untouched_returns_none():
    from thedas_io.export import _texture_bytes

    rgba = np.full((8, 8, 4), (10, 20, 30, 255), dtype=np.uint8)
    # source TGA decodes to the same pixels the live image holds
    raw = _tga((10, 20, 30, 255))
    item = {
        "file": "t_0d.tga",
        "kind": "texture",
        "raw": raw,
        "objects": [_LiveImage("t_0d.tga", rgba)],
    }
    assert _texture_bytes(item) is None


def test_upscaled_live_exports_at_live_size():
    from thedas_io.export import _texture_bytes

    raw = _tga((10, 20, 30, 255), (8, 8))  # stock 8x8
    rgba = np.full((16, 16, 4), (200, 100, 50, 255), dtype=np.uint8)
    item = {
        "file": "t_0d.tga",
        "kind": "texture",
        "raw": raw,
        "objects": [_LiveImage("t_0d.tga", rgba)],
    }
    out = _texture_bytes(item)  # must not raise "expected sequence size"
    assert out is not None
    got = Image.open(io.BytesIO(out))
    got.load()
    assert tuple(got.size) == (16, 16)


def test_reload_autoscales_to_file_size(tmp_path, monkeypatch):
    from thedas_io import ui

    rgba = np.full((8, 8, 4), (10, 20, 30, 255), dtype=np.uint8)
    live = _LiveImage("t_0d.tga", rgba)
    cache = tmp_path / "thedas_io_textures"
    cache.mkdir()
    Image.new("RGBA", (16, 16), (200, 100, 50, 255)).save(cache / "t_0d.tga", "PNG")

    monkeypatch.setattr(ui, "_cache_dir", lambda: cache)
    monkeypatch.setattr(ui, "_texture_images", lambda: [(live, "t_0d.tga")])
    done, _missing, failed = ui.reload_textures()
    assert done == ["t_0d.tga"]
    assert failed == []
    assert live.scaled == [(16, 16)]
    assert live.packed == 1


def _dxt5_raw():
    # Minimal DDS header with a DXT5 fourcc; payload unused by the encoder.
    import struct

    raw = bytearray(128)
    raw[0:4] = b"DDS "
    struct.pack_into("<I", raw, 80, 0x4)  # DDPF_FOURCC, no DDPF_RGB
    raw[84:88] = b"DXT5"
    return bytes(raw)


def test_dxt_reencode_from_formatless_image():
    # nvtt path: fromarray images carry format None; must still encode.
    from thedas_io.export import _encode_texture

    pil = Image.fromarray(np.full((8, 8, 4), (200, 100, 50, 255), dtype=np.uint8))
    assert pil.format is None
    out = _encode_texture(_dxt5_raw(), pil, (8, 8), "diffuse")
    assert out[:4] == b"DDS "
