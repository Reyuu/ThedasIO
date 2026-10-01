from __future__ import annotations

import math

from conftest import DATA_DIR
from thedas_io.geometry import (
    bone_names,
    compose,
    flatten,
    node_local,
    quat_rotate,
)
from thedas_io.mmh.description import load

MMH_PATH = DATA_DIR / "c_corspidra_0.mmh"


def approx(a, b, tol=1e-5):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_quat_rotate():
    assert approx(quat_rotate((0, 0, 0, 1), (1, 2, 3)), (1, 2, 3))
    assert approx(quat_rotate((0, 0, math.sqrt(0.5), math.sqrt(0.5)), (1, 0, 0)), (0, 1, 0))


def test_compose_identity():
    loc = (1.0, 2.0, 3.0)
    assert compose(((0, 0, 0), (0, 0, 0, 1)), (loc, (0, 0, 0, 1)))[0] == loc


def test_node_local_defaults():
    assert node_local({}) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    assert node_local({6047: (1, 2, 3, 1)})[0] == (1.0, 2.0, 3.0)


def test_flatten_spine():
    tree = load(MMH_PATH)
    rows = flatten(tree)
    assert len(rows) == 394
    assert rows[0][0] is tree and rows[0][1] is None
    assert rows[0][2] == (0.0, 0.0, 0.0)
    by_name = {n.name: (p.name if p else None) for n, p, _, _ in rows if n.name}
    assert by_name["GOB"] == "c_corspidra_0.mmh"
    assert by_name["GOD"] == "GOB"
    assert by_name["Root"] == "GOD"
    gob = tree.find("GOB")
    assert gob in [n for n, p, _, _ in rows if p is tree]
    assert tree.find("Root").name == "Root"


def test_bone_names():
    names = bone_names(load(MMH_PATH))
    assert len(names) == 66
    assert sorted(names) == list(range(66))
    assert all(isinstance(n, str) for n in names.values())
