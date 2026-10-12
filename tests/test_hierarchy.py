from __future__ import annotations

import math

from conftest import DATA_DIR
from thedas_io.geometry import (
    bone_frame,
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


def test_compose_applies_the_parent_rotation_after_the_local_one():
    # world = parent * local. swapping the two looks the same on a flat chain
    # and only diverges with depth, which is why it went unnoticed for so long.
    half = math.sqrt(0.5)
    rz = (0.0, 0.0, half, half)  # +90 about Z
    rx = (half, 0.0, 0.0, half)  # +90 about X

    _, world = compose(((0, 0, 0), rz), ((0, 0, 0), rx))
    # X first (leaves +X alone), then Z: +X ends up on +Y
    assert approx(quat_rotate(world, (1, 0, 0)), (0, 1, 0))
    # the other order would put it on +Z
    assert not approx(quat_rotate(world, (1, 0, 0)), (0, 0, 1))


def test_deep_chain_does_not_drift():
    # the failure mode: a wrong multiply order compounds per level, so a deep
    # chain of turns lands somewhere no amount of rotating would explain
    half = math.sqrt(0.5)
    rz = (0.0, 0.0, half, half)  # +90 about Z
    step = ((0, 0, 0), rz)
    here = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    for _ in range(4):
        here = compose(step, here)
    # four 90-degree turns about Z come back to the start
    assert approx(quat_rotate(here[1], (1, 0, 0)), (1, 0, 0))
    for _ in range(4):
        here = compose(step, here)
    assert approx(quat_rotate(here[1], (1, 0, 0)), (1, 0, 0))


def test_inverse_compose_round_trips_through_compose():
    from thedas_io.export import _inverse_compose

    parent = ((1.5, -0.25, 0.75), (0.1, 0.2, 0.3, 0.9))
    local = ((0.3, 0.4, -0.2), (0.3, -0.1, 0.2, 0.9))
    parent = (parent[0], _unit(parent[1]))
    local = (local[0], _unit(local[1]))
    back = _inverse_compose(parent, compose(parent, local))
    assert approx(back[0], local[0])
    assert approx(back[1], local[1])


def _unit(q):
    n = math.sqrt(sum(v * v for v in q))
    return tuple(v / n for v in q)


def test_bone_frame_is_right_handed_for_every_bone():
    # a left-handed bone frame mirrors the mesh it deforms, so this is not a
    # matter of taste: the alternative axis permutation fails it on all 68
    for _n, _p, _l, q in flatten(load(MMH_PATH)):
        if q == (0.0, 0.0, 0.0, 1.0):
            continue
        assert _det3(*bone_frame(q)) > 0.999, f"left-handed frame for {q}"


def test_bone_frame_is_orthonormal():
    # the mmh stores quaternions at float32 precision, so unit length and
    # orthogonality hold to about 1e-5 rather than exactly
    for _n, _p, _l, q in flatten(load(MMH_PATH)):
        x, y, z = bone_frame(q)
        for a in (x, y, z):
            assert abs(math.sqrt(sum(v * v for v in a)) - 1.0) < 1e-5
        assert abs(sum(x[i] * y[i] for i in range(3))) < 1e-5
        assert abs(sum(x[i] * z[i] for i in range(3))) < 1e-5
        assert abs(sum(y[i] * z[i] for i in range(3))) < 1e-5


def test_bone_frame_bone_y_follows_the_node_local_x():
    # the length axis was never in doubt; pin it so the roll change cannot move it
    for _n, _p, _l, q in flatten(load(MMH_PATH)):
        _bx, by, _bz = bone_frame(q)
        assert approx(by, quat_rotate(q, (1, 0, 0)))


def test_bone_frame_mirrors_left_to_right():
    # the model is bilaterally symmetric about x = 0, so a correct frame has to
    # agree with its mirror image to well under a degree. this is the check that
    # rejects the other permutation, which sits 60 degrees out.
    frames = {}
    heads = {}
    for n, _p, l, q in flatten(load(MMH_PATH)):
        if n.kind == "node" and n.name:
            frames[n.name] = bone_frame(q)
            heads[n.name] = l
    pairs = []
    for name, l in heads.items():
        if not name.startswith("L"):
            continue
        other = heads.get("R" + name[1:])
        if other is None:
            continue
        if (abs(l[0] + other[0]) < 1e-3
                and abs(l[1] - other[1]) < 1e-3
                and abs(l[2] - other[2]) < 1e-3):
            pairs.append((name, frames[name], frames["R" + name[1:]]))
    assert len(pairs) >= 15, f"expected mirror pairs, found {len(pairs)}"
    for name, a, b in pairs:
        mirrored = [tuple(-v[0] if i == 0 else v[i] for i in range(3)) for v in b]
        # mirroring flips handedness, so exactly one axis takes a sign flip
        assert approx(a[1], mirrored[1], 1e-2), f"{name}: Y axis not mirrored"
        assert approx(a[0], tuple(-c for c in mirrored[0]), 1e-2), f"{name}: X axis"
        assert approx(a[2], mirrored[2], 1e-2), f"{name}: Z axis"


def test_bone_heads_sit_on_the_geometry_they_deform():
    # the one check that can catch a wrong compose order, because it compares the
    # rig against the mesh weights rather than against itself. a rig test that
    # reads the same flatten() the importer uses cannot see this class of bug.
    from thedas_io.geometry import decode_chunk
    from thedas_io.msh.description import MSHReader

    tree = load(MMH_PATH)
    rows = flatten(tree)
    mshhs = [n for n, _p, _l, _q in rows if n.kind == "mshh"]
    order = [v for v in (mshhs[0].values.get(6255) or []) if isinstance(v, int)]
    wanted = {n.values.get(6006) for n in mshhs}
    assert order, "no bone order on the mshh"

    clouds: dict = {}
    every = []
    for msh in sorted(DATA_DIR.glob("c_corspidr*.msh")):
        mesh = MSHReader(msh.read_bytes()).mesh
        for chunk in mesh.chunks:
            if chunk.name not in wanted:
                continue
            build = decode_chunk(mesh, chunk)
            every.extend(build["positions"])
            for local, members in build["groups"].items():
                g = order[local] if 0 <= local < len(order) and isinstance(order[local], int) else local
                clouds.setdefault(g, []).extend(build["positions"][v] for v, _w in members)
    assert clouds, "no weighted vertices decoded"

    diag = math.dist(
        [min(p[i] for p in every) for i in range(3)],
        [max(p[i] for p in every) for i in range(3)],
    )
    depths = {}
    for n, _p, _l, _q in rows:
        depths[id(n)] = 0 if _p is None else depths[id(_p)] + 1

    deep = []
    for n, _p, l, _q in rows:
        if n.kind != "node" or depths[id(n)] < 6:
            continue
        pts = clouds.get(n.values.get(6254))
        if not pts or len(pts) < 8:
            continue
        centre = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
        deep.append(math.dist(centre, l))
    assert len(deep) >= 5, f"expected deep bones, found {len(deep)}"
    mean = sum(deep) / len(deep)
    # 0.129 m against a 2.96 m model. the wrong multiply order gives 0.51 m.
    assert mean < 0.10 * diag, f"deep bones drift {mean:.3f} m on a {diag:.2f} m model"


def _det3(x, y, z):
    return (x[0] * (y[1] * z[2] - y[2] * z[1])
            - x[1] * (y[0] * z[2] - y[2] * z[0])
            + x[2] * (y[0] * z[1] - y[1] * z[0]))


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
