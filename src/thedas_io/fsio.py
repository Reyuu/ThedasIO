# Write a file so a failure never leaves a half-written one behind.

from __future__ import annotations

import io
import os
import time
from pathlib import Path

# Windows ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION. Antivirus and indexers
# hold a just-written file briefly; anything else is a real permission problem.
_TRANSIENT_WINERRORS = (32, 33)


def _sharing_violation(exc: PermissionError) -> bool:
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def write_bytes_atomic(path, data: bytes, attempts: int = 3, delay: float = 0.05) -> Path:
    """Write ``data`` to ``path``; the old file survives anything going wrong."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        for attempt in range(attempts):
            try:
                with open(tmp, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
                return path
            except PermissionError as exc:
                # retrying a plain access-denied just hides it and leaves temp
                # files behind; only a sharing violation is worth waiting out
                if not _sharing_violation(exc) or attempt + 1 >= attempts:
                    raise
                time.sleep(delay)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return path


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> Path:
    return write_bytes_atomic(path, text.encode(encoding))


def save_image_atomic(image, path, image_format: str) -> Path:
    # PIL writes straight to the path, so an encoder that dies partway would
    # leave the user's old PNG destroyed; a buffer keeps it in memory instead
    buf = io.BytesIO()
    image.save(buf, image_format)
    return write_bytes_atomic(path, buf.getvalue())
