# Write a file so a failure never leaves a half-written one behind.

from __future__ import annotations

import io
import os
from pathlib import Path


def write_bytes_atomic(path, data: bytes) -> Path:
    """Write ``data`` to ``path``; the old file survives anything going wrong."""
    path = Path(path)
    last: Exception | None = None
    for suffix in ("", ".1", ".2", ".3"):
        tmp = path.with_name(path.name + ".tmp" + suffix)
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return path
        except PermissionError as exc:
            last = exc
            try:
                tmp.unlink()
            except OSError:
                pass
            continue
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    if last is not None:
        raise last
    return path


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> Path:
    return write_bytes_atomic(path, text.encode(encoding))


def save_image_atomic(image, path, image_format: str) -> Path:
    # PIL writes straight to the path, so an encoder that dies partway would
    # leave the user's old PNG destroyed; a buffer keeps it in memory instead
    buf = io.BytesIO()
    image.save(buf, image_format)
    return write_bytes_atomic(path, buf.getvalue())
