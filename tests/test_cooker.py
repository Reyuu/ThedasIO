# mesh cooker tests; cooking cases need THEDAS_COOK_DLL set to a real nxcooking.dll

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from conftest import DATA_DIR
from thedas_io.phy import cooker, nxs
from thedas_io.phy.description import PhyReader

DLL_ENV = "THEDAS_COOK_DLL"
TETRA = (
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
    [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)],
)


def game_dir() -> Path:
    dll = Path(os.environ[DLL_ENV])
    # bin_ship copy, or the game folder itself
    return dll.parent.parent if dll.parent.name.lower() == "bin_ship" else dll.parent


needs_dll = pytest.mark.skipif(
    not os.environ.get(DLL_ENV),
    reason=f"set {DLL_ENV} to the game's nxcooking.dll to run the cooker",
)


def shipped_mesh():
    reader = PhyReader((DATA_DIR / "cai_lrgcrna_0.phy").read_bytes())
    return next(s.mesh for s in reader.shapes if s.kind == "mshs" and s.mesh is not None)


def test_stream_params_reads_a_shipped_header():
    params = cooker.stream_params(shipped_mesh().raw)
    assert params["edge"] == pytest.approx(0.001)
    assert params["axis"] == 0xFF  # not a height field
    assert params["extent"] == pytest.approx(0.0)


def test_game_dll_finds_the_copy_beside_the_game(tmp_path):
    (tmp_path / "bin_ship").mkdir()
    dll = tmp_path / "bin_ship" / "nxcooking.dll"
    dll.write_bytes(b"")
    assert cooker.game_dll(game_dir=tmp_path) == dll


def test_game_dll_falls_back_to_the_asset_database(tmp_path):
    game = tmp_path / "Dragon Age"
    (game / "bin_ship").mkdir(parents=True)
    dll = game / "bin_ship" / "nxcooking.dll"
    dll.write_bytes(b"")
    packaged = game / "packages" / "core" / "assets"
    packaged.mkdir(parents=True)

    db = tmp_path / "assets.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("create table assets (path text)")
        conn.execute("insert into assets values (?)", (str(packaged),))

    assert cooker.game_dll(db_path=db) == dll


def test_ships_the_cooker_executable():
    # ships ready; nothing compiles at run time
    assert cooker.HELPER.is_file(), "cook.exe is missing from the add-on"
    assert cooker.HELPER.read_bytes()[:2] == b"MZ"


def test_game_dll_picks_the_games_own_cooker(tmp_path):
    (tmp_path / "bin_ship").mkdir()
    dao = tmp_path / "bin_ship" / "nxcooking.dll"
    da2 = tmp_path / "bin_ship" / "physxcooking.dll"
    dao.write_bytes(b"")
    da2.write_bytes(b"")
    assert cooker.game_dll(game_dir=tmp_path, game="DAO") == dao
    assert cooker.game_dll(game_dir=tmp_path, game="DA2") == da2


def test_missing_library_is_a_clear_error(tmp_path):
    with pytest.raises(cooker.CookerError, match="cooking.dll not found"):
        cooker.cook_triangle_mesh(*TETRA, game_dir=tmp_path)


@needs_dll
def test_cooks_a_tetrahedron():
    verts, tris = TETRA
    raw = cooker.cook_triangle_mesh(verts, tris, game_dir=game_dir())

    assert raw.startswith(cooker.MAGIC)
    mesh = nxs.parse(raw)
    assert mesh.vertex_count == len(verts)
    assert mesh.tri_count == len(tris)
    # the stream really carries an accelerator, not just geometry
    info = nxs.accelerator(raw)
    assert info["nodes"] > 0
    assert info["quantum"] == pytest.approx(0.001, rel=1e-3)


@needs_dll
def test_cooks_a_shipped_mesh_to_the_same_stream():
    # the fixture is already cooked, so PhysX may re-weld it; same mesh and quantum
    mesh = shipped_mesh()
    cooked = cooker.cook_triangle_mesh(
        mesh.vertices, mesh.tris, game_dir=game_dir(), **cooker.stream_params(mesh.raw)
    )

    assert len(cooked) == len(mesh.raw)
    parsed = nxs.parse(cooked)
    assert parsed.vertex_count == mesh.vertex_count
    assert parsed.tri_count == mesh.tri_count
    before, after = nxs.accelerator(mesh.raw), nxs.accelerator(cooked)
    assert after["bits"] == before["bits"]
    assert after["quantum"] == pytest.approx(before["quantum"], rel=1e-6)
    assert sorted(after["sections"]) == sorted(before["sections"])
