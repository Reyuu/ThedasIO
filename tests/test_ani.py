"""Tests for the ``.ani`` animation reader.

Two real fixtures: a 84-node character anim (``c_dog.sit_p.ani``, 64-bit
rotations and translations) and a door clip (``prp_door.dying.ani``, 32-bit
rotations) - the whole point of the second is that the 32-bit quaternion pack
decodes as cleanly as the 64-bit one.
"""

from __future__ import annotations

import math
import struct

from conftest import DATA_DIR, fourcc
from thedas_io.ani.description import (
    ROTATION32,
    ROTATION64,
    TRANSLATION,
    ANIReader,
    patch_ani,
)
from thedas_io.gff40.description import FLAGS, GFFField, GFFReader, GFFStructDef, GFFWriter

DOG = DATA_DIR / "c_dog.sit_p.ani"
DOOR = DATA_DIR / "prp_door.dying.ani"


def norm(q):
    return math.sqrt(sum(c * c for c in q))


def test_c_dog_parses_its_channels():
    reader = ANIReader(DOG.read_bytes())
    assert reader.name == "c_dog.sit_p.ani"
    assert abs(reader.length - 0.9333329) < 1e-5
    kinds = reader.summary()["kinds"]
    assert kinds == {"rotation": 77, "position": 7}
    assert len(reader.nodes) == 84
    chest = reader.animation.by_name("chest_rotation")
    assert chest is not None and chest.kind == "rotation"


def test_door_clip_uses_32_bit_rotations():
    reader = ANIReader(DOOR.read_bytes())
    assert reader.summary()["kinds"] == {"rotation": 1, "position": 3}
    rot = next(n for n in reader.nodes if n.target == ROTATION32)
    pos = next(n for n in reader.nodes if n.target == TRANSLATION)
    assert rot.kind == "rotation" and pos.kind == "position"


def test_every_rotation_key_is_exactly_unit():
    for path in (DOG, DOOR):
        reader = ANIReader(path.read_bytes())
        for node in reader.nodes:
            if node.target not in (ROTATION32, ROTATION64):
                continue
            for _t, q in node.keys:
                assert abs(norm(q) - 1.0) < 1e-4, (path.name, node.name)


def test_keytime_converts_to_seconds():
    reader = ANIReader(DOG.read_bytes())
    # keytime is a u16 fraction of the animation length
    node = reader.animation.by_name("eye_left_rotation")
    times = [t for t, _ in node.keys]
    assert 0.0 in times
    assert max(times) <= reader.length + 1e-9
    # a key at the very end reaches the animation length exactly
    ends = [t for t, _ in node.keys if abs(t - reader.length) < 1e-9]
    assert ends


def test_first_keys_match_the_mmh_rest_pose():
    # the dog's first keys sit on its MMH rest rotations; a wrong packed layout
    # (e.g. index bits or field offsets) lands ~180 degrees off instead
    from thedas_io.geometry import node_local
    from thedas_io.mmh.description import load

    rest = {
        n.name.lower(): node_local(n.values)[1]
        for n in load(DATA_DIR / "c_doga_0.mmh").walk()
        if n.name
    }
    close = 0
    for node in ANIReader(DOG.read_bytes()).nodes:
        if node.target != ROTATION64:
            continue
        r = rest.get(node.name.removesuffix("_rotation").lower())
        if r is None:
            continue
        dot = abs(sum(a * b for a, b in zip(node.keys[0][1], r)))
        close += 2 * math.degrees(math.acos(min(1.0, dot))) < 1.0
    assert close >= 40


def test_index_bit_wide_rotations_are_still_unit():
    # decode_quat32 on the door clip's actual words, across all keys
    reader = ANIReader(DOOR.read_bytes())
    for node in reader.nodes:
        if node.target != ROTATION32:
            continue
        for _t, q in node.keys:
            assert abs(norm(q) - 1.0) < 1e-3


def test_position_keys_are_three_floats():
    reader = ANIReader(DOOR.read_bytes())
    pos = next(n for n in reader.nodes if n.target == TRANSLATION)
    for _t, v in pos.keys:
        assert len(v) == 3 and all(isinstance(c, float) for c in v)


def _random_unit(rng):
    v = [rng.gauss(0, 1) for _ in range(4)]
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _angle(a, b):
    return 2 * math.acos(min(1.0, abs(sum(x * y for x, y in zip(a, b)))))


def test_encode_quat64_inverts_decode():
    import random

    from thedas_io.ani.description import decode_quat64, encode_quat64

    rng = random.Random(1)
    worst = max(
        _angle(q, decode_quat64(encode_quat64(q))) for q in (_random_unit(rng) for _ in range(2000))
    )
    assert worst < 1e-5


def test_encode_quat32_inverts_decode():
    import random

    from thedas_io.ani.description import decode_quat32, encode_quat32

    rng = random.Random(2)
    worst = max(
        _angle(q, decode_quat32(encode_quat32(q))) for q in (_random_unit(rng) for _ in range(2000))
    )
    assert worst < 5e-3  # 10-bit fields: ~0.0014 step per component


def test_patch_without_changes_is_byte_identical():
    from thedas_io.ani.description import patch_ani

    raw = DOG.read_bytes()
    assert patch_ani(raw, lambda node: None) == raw


def test_patch_rewrites_only_the_given_key():
    from thedas_io.ani.description import patch_ani

    raw = DOG.read_bytes()
    before_anim = ANIReader(raw).animation
    before = before_anim.by_name("chest_rotation")
    target = (0.0, 0.0, 0.0, 1.0)

    def values(node):
        if node.name != "chest_rotation":
            return None
        own = [t for t, _ in node.keys]
        return own, [target] + [None] * (len(node.keys) - 1)

    patched = patch_ani(raw, values)
    assert len(patched) == len(raw)
    after = ANIReader(patched).animation
    chest = after.by_name("chest_rotation")
    assert _angle(chest.keys[0][1], target) < 1e-5
    for (_t, old), (_u, new) in zip(before.keys[1:], chest.keys[1:]):
        assert old == new  # untouched keys keep their decoded values exactly
    # every other channel is unchanged
    for node in after.nodes:
        if node.name == "chest_rotation":
            continue
        assert before_anim.by_name(node.name).keys == node.keys


def test_patch_adds_a_key_between_the_originals_and_extends_the_clip():
    from thedas_io.ani.description import patch_ani

    raw = DOG.read_bytes()
    before = ANIReader(raw).animation
    new_q = (0.0, 0.0, 0.0, 1.0)

    def values(node):
        own = [t for t, _ in node.keys]
        old = [v for _, v in node.keys]
        if node.name != "chest_rotation":
            return own, old
        times = own + [3.5]
        return sorted(times), [v for _t, v in sorted(zip(times, old + [new_q]))]

    after = ANIReader(patch_ani(raw, values, 4.75)).animation
    assert abs(after.length - 4.75) < 1e-6
    chest = after.by_name("chest_rotation").keys
    assert len(chest) == len(before.by_name("chest_rotation").keys) + 1
    t, q = min(chest, key=lambda k: abs(k[0] - 3.5))
    assert abs(t - 3.5) < 1e-4 and _angle(q, new_q) < 1e-5
    for node in before.nodes:
        if node.name == "chest_rotation":
            continue
        kept = after.by_name(node.name).keys
        assert len(kept) == len(node.keys)
        assert all(abs(a[0] - b[0]) < 1e-3 for a, b in zip(kept, node.keys))


def _vfx_clip() -> bytes:
    # the door clip plus one scalar vfx channel, built the way a real one is:
    # a float key struct, a wrapper holding the key array, and a node (target
    # 8) pointing at it. None of the two shipped fixtures carry a vfx channel.
    reader = GFFReader(DOOR.read_bytes())
    writer = GFFWriter.from_reader(reader)

    # same shape as the packed-quat key def, except KeyData0 is an f32
    key_def = GFFStructDef(fourcc("vfxk"), 2, 0, 6)
    key_def.field_instances.append(GFFField(4035, 2, 0, 0))  # KeyTime u16
    key_def.field_instances.append(GFFField(4036, 8, 0, 2))  # KeyData0 f32
    key_index = len(writer.struct_defs)
    writer.struct_defs.append(key_def)

    wrap_def = GFFStructDef(fourcc("vfxw"), 1, 0, 4)
    wrap_def.field_instances.append(GFFField(4004, key_index, FLAGS["STRUCT_ARRAY"], 0))
    wrap_index = len(writer.struct_defs)
    writer.struct_defs.append(wrap_def)

    rows = [(0, 1.5), (32768, 2.5), (65535, 3.5)]
    blob = struct.pack("<I", len(rows))
    for key_time, sample in rows:
        blob += struct.pack("<Hf", key_time, sample)
    wrap_offset = writer.alloc(struct.pack("<I", writer.alloc(blob)))

    node = bytearray(16)
    struct.pack_into("<I", node, 0, writer.alloc_ecstring("vfx_birthrate"))
    node[12] = 8
    struct.pack_into("<II", node, 4, (FLAGS["REFERENCE"] << 16) | wrap_index, wrap_offset)

    nodes_ref = struct.unpack_from("<I", reader.data, reader.data_offset + 12)[0]
    count = struct.unpack_from("<I", reader.data, reader.data_offset + nodes_ref)[0]
    existing = bytes(writer.data_block[nodes_ref + 4 : nodes_ref + 4 + count * 16])
    list_offset = writer.alloc(struct.pack("<I", count + 1) + existing + bytes(node))
    struct.pack_into("<I", writer.data_block, 12, list_offset)
    return writer.write()


def test_the_fixture_carries_a_scalar_vfx_channel():
    anim = ANIReader(_vfx_clip()).animation
    vfx = anim.by_name("vfx_birthrate")
    assert vfx is not None and vfx.target == 8 and vfx.kind == "scalar"
    assert [v for _t, v in vfx.keys] == [1.5, 2.5, 3.5]


def test_a_vfx_channel_does_not_block_a_clip_extension():
    # every key time is a fraction of the length, so a longer clip has to
    # rewrite the vfx node's key list too
    raw = _vfx_clip()
    before = ANIReader(raw).animation
    vfx = before.by_name("vfx_birthrate")

    def values(node):
        own = [t for t, _ in node.keys]
        return own, [v for _, v in node.keys]

    after = ANIReader(patch_ani(raw, values, 4.0)).animation
    assert abs(after.length - 4.0) < 1e-6

    grown = after.by_name("vfx_birthrate")
    assert len(grown.keys) == len(vfx.keys)
    # the key list is rebuilt at the new length, but the samples stay put and
    # land on the same second (one u16 step of the longer clip is ~6e-5 s)
    for (old_t, old_v), (new_t, new_v) in zip(vfx.keys, grown.keys):
        assert abs(old_v - new_v) < 1e-6
        assert abs(old_t - new_t) < 1e-4


def test_saturated_quat_fields_still_decode_to_unit_length():
    from thedas_io.ani.description import decode_quat32, decode_quat64

    # all three fields at their maximum is not a real key, but it must not
    # decode to a quaternion off the unit sphere
    assert abs(norm(decode_quat32(0xFFFFFFFF)) - 1.0) < 1e-9
    assert abs(norm(decode_quat64(0xFFFFFFFFFFFFFFFF)) - 1.0) < 1e-9
