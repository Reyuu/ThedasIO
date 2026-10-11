"""Corpus regression: decode every .msh in a real install.

Opt-in, because it needs a game folder and walks thousands of files:

    THEDAS_MSH_CORPUS="F:\\Modding\\DAO\\overridelink" uv run pytest -m corpus

Worth running after anything touching index width or declarators. The
index-width rule was wrong for thirteen shipped meshes -- 96795 indices over
22105 vertices, stored as u16 -- and no fixture in data/ looked like them, so
nothing in the fast suite noticed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from check_msh_corpus import check

pytestmark = pytest.mark.corpus


def _corpus() -> str | None:
    root = os.environ.get("THEDAS_MSH_CORPUS")
    return root if root and Path(root).is_dir() else None


def test_every_shipped_mesh_decodes():
    root = _corpus()
    if root is None:
        pytest.skip("set THEDAS_MSH_CORPUS to a folder of .msh files")

    files, chunks, failures = check(root)

    assert files, f"no .msh found under {root}"
    assert failures == [], f"{len(failures)} of {chunks} chunks failed to decode"
