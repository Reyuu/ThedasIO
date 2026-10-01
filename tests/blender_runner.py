"""Run snippets inside real headless Blender; pytest stays the runner.

Snippets define main() returning JSON-serializable data. Skips when
no Blender is found (BLENDER_EXE or PATH).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MARKER = "__THEDAS_IO_RESULT__"


def _exe():
    path = os.environ.get("BLENDER_EXE") or shutil.which("blender")
    if path and os.path.exists(path):
        return path
    fallback = r"F:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
    return fallback if os.path.exists(fallback) else None


def run(source: str, timeout=300):
    exe = _exe()
    if exe is None:
        pytest.skip(
            "Blender not found (set BLENDER_EXE); these carry the "
            "`blender` marker, so run with -m blender to select them"
        )
    script = (
        "import json\nimport sys\n"
        "for _m in [m for m in list(sys.modules) if m == 'thedas_io' "
        f"or m.startswith('thedas_io.')]:\n"
        f"    del sys.modules[_m]\n{source}\nprint('{MARKER}', json.dumps(main()))\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        name = f.name
    try:
        proc = subprocess.run(
            [exe, "--background", "--python", name],
            capture_output=True,
            timeout=timeout,
            cwd=ROOT,
            check=False,
        )
    finally:
        os.unlink(name)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    lines = [l for l in out.splitlines() if l.startswith(MARKER)]
    if not lines:
        pytest.fail(f"no result from Blender:\n{out[-2000:]}\n{err[-2000:]}")
    return json.loads(lines[-1][len(MARKER) :].strip())
