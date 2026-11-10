"""ANI reader hardening: malformed files, empty tracks and bad keys.

The corpus reader must keep going on a damaged channel and tell the caller what
it skipped; a non-ANI GFF must be rejected by name.
"""

from __future__ import annotations

import struct

import pytest

from conftest import DATA_DIR
from thedas_io.ani.description import (
    L_KEY_DATA0,
    L_KEY_DATA1,
    L_KEY_DATA2,
    L_KEY_TIME,
    L_NODE_DATA,
    L_NODE_NAME,
    L_NODE_TARGET,
    L_NODES,
    ROTATION48,
    ROTATION64,
    TRANSLATION,
    ANIReader,
    _decode_node,
    decode_key,
    decode_quat48,
    encode_quat48,
)
from thedas_io.gff40.description import GFFReader

DOG = DATA_DIR / "c_dog.sit_p.ani"


def _node(target=8, name="scalar", rows=None, data=True):
    node = {L_NODE_NAME: name, L_NODE_TARGET: target}
    if data:
        node[L_NODE_DATA] = {"value": {L_NODE_DATA: rows if rows is not None else []}}
    return node


def test_reader_rejects_a_non_ani_gff():
    with pytest.raises(ValueError, match="not an ANI file"):
        ANIReader((DATA_DIR / "c_doga_0.mmh").read_bytes())


def test_reader_handles_an_empty_node_list():
    raw = bytearray(DOG.read_bytes())
    reader = GFFReader(bytes(raw))
    field = next(f for f in reader.struct_defs[0].field_instances if f.label_id == L_NODES)
    ref = struct.unpack_from("<I", raw, reader.data_offset + field.data_index)[0]
    struct.pack_into("<I", raw, reader.data_offset + ref, 0)

    parsed = ANIReader(bytes(raw))
    assert parsed.nodes == []
    assert parsed.summary()["nodes"] == 0


def test_bad_nodes_are_skipped_with_a_reason():
    skipped, warnings = [], []
    no_data = _node(name="no_data", data=False)
    no_target = {L_NODE_NAME: "no_target", L_NODE_DATA: {"value": {L_NODE_DATA: []}}}
    bad_target = _node(target="nope", name="bad_target")
    empty = _node(name="empty")
    no_time = _node(name="no_time", rows=[{L_KEY_DATA0: 1.5}])

    assert _decode_node(no_data, 1.0, skipped, warnings) is None
    assert _decode_node(no_target, 1.0, skipped, warnings) is None
    assert _decode_node(bad_target, 1.0, skipped, warnings) is None
    assert _decode_node(empty, 1.0, skipped, warnings) is None
    assert _decode_node(no_time, 1.0, skipped, warnings) is None
    assert [reason for _name, reason in skipped] == [
        "no node data",
        "no target",
        "bad target 'nope'",
        "no keys",
        "key has no time",
        "no keys",
    ]


def test_rotation_key_without_a_packed_word_is_skipped():
    skipped, warnings = [], []
    node = _node(
        target=ROTATION64,
        name="bad_rot",
        rows=[{L_KEY_TIME: 0, L_KEY_DATA0: 1.5}],
    )
    assert _decode_node(node, 1.0, skipped, warnings) is None
    assert skipped == [
        ("bad_rot", "rotation key has no packed word"),
        ("bad_rot", "no keys"),
    ]


def test_non_monotonic_times_are_kept_but_warned():
    skipped, warnings = [], []
    node = _node(
        name="out_of_order",
        rows=[
            {L_KEY_TIME: 100, L_KEY_DATA0: 1.0},
            {L_KEY_TIME: 50, L_KEY_DATA0: 2.0},
        ],
    )
    decoded = _decode_node(node, 1.0, skipped, warnings)
    assert decoded is not None and len(decoded.keys) == 2
    assert warnings == [("out_of_order", "key times decrease")]


def test_translation_keys_tolerate_missing_components():
    assert decode_key(TRANSLATION, {}) == (0.0, 0.0, 0.0)
    assert decode_key(TRANSLATION, {L_KEY_DATA0: None, "bad": "x"}) == (0.0, 0.0, 0.0)
    assert decode_key(TRANSLATION, {L_KEY_DATA0: 1, "bad": 2}) == (1.0, 0.0, 0.0)


def test_quat48_round_trips_and_stays_unit():
    import math
    import random

    rng = random.Random(3)
    worst = 0.0
    for _ in range(2000):
        v = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in v))
        q = tuple(c / n for c in v)
        if q[3] < 0:
            q = tuple(-c for c in q)
        code = encode_quat48(q)
        back = decode_quat48(*code)
        assert abs(math.sqrt(sum(c * c for c in back)) - 1.0) < 1e-6
        dot = min(1.0, abs(sum(a * b for a, b in zip(q, back))))
        worst = max(worst, 2 * math.acos(dot))
    assert worst < 1e-2  # w is reconstructed; near-180° keys lose more


def test_quat48_place_comes_from_the_low_bits():
    # DAOLib's case: place 1 (first low bit 0, second 1), largest goes second
    assert decode_quat48(32768, 32769, 32768) == pytest.approx((0.0, 1.0, 0.0, 0.0), abs=1e-4)


def test_target_48_is_a_rotation_with_three_u16_fields():
    skipped, warnings = [], []
    node = _node(
        target=ROTATION48,
        name="da2_rot",
        rows=[
            {
                L_KEY_TIME: 0,
                L_KEY_DATA0: 32769,
                L_KEY_DATA1: 32769,
                L_KEY_DATA2: 32768,
            },
            {
                L_KEY_TIME: 32768,
                L_KEY_DATA0: 32769,
                L_KEY_DATA1: 32769,
                L_KEY_DATA2: 32768,
            },
        ],
    )
    decoded = _decode_node(node, 2.0, skipped, warnings)
    assert decoded is not None and decoded.kind == "rotation"
    assert decoded.keys[0][1] == pytest.approx((0.0, 0.0, 0.0, 1.0), abs=1e-4)
    assert skipped == [] and warnings == []
