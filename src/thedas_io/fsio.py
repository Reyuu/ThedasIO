# Write a file so a failure never leaves a half-written one behind.

from __future__ import annotations

import os
from pathlib import Path


def write_bytes_atomic(path, data: bytes) -> Path:
    """Write ``data`` to ``path``; the old file survives anything going wrong."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # a crash mid-write leaves the temp file; it is never the real one
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> Path:
    return write_bytes_atomic(path, text.encode(encoding))