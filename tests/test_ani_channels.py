# da2 channel ids: a missing, malformed or partial table maps nothing, and only
# numeric ids count as unmapped (DAO clips name their channels).

from __future__ import annotations

import json

from thedas_io.ani.channels import load_table, resolve, unmapped


def test_missing_table_maps_nothing(tmp_path):
    assert load_table(tmp_path / "absent.json") == {}


def test_table_reads_ids_as_text(tmp_path):
    path = tmp_path / "table.json"
    path.write_text(json.dumps({"506703156": "gob_gobtranslation"}), encoding="utf-8")
    assert load_table(path) == {"506703156": "gob_gobtranslation"}


def test_malformed_table_maps_nothing(tmp_path):
    path = tmp_path / "table.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_table(path) == {}


def test_table_that_is_not_an_object_maps_nothing(tmp_path):
    path = tmp_path / "table.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_table(path) == {}


def test_table_drops_empty_entries(tmp_path):
    path = tmp_path / "table.json"
    path.write_text(json.dumps({"1": "", "2": "root_rotation"}), encoding="utf-8")
    assert load_table(path) == {"2": "root_rotation"}


def test_resolve_keeps_unknown_and_string_names():
    table = {"506703156": "gob_gobtranslation"}
    assert resolve("506703156", table) == "gob_gobtranslation"
    assert resolve("root_rotation", table) == "root_rotation"
    assert resolve(12345, {}) == "12345"


def test_unmapped_reports_numeric_ids_once_in_order():
    table = {"2": "root_rotation"}
    names = ["506703156", "2", "root_rotation", "506703156", 7]
    assert unmapped(names, table) == ["506703156", "7"]


def test_unmapped_is_empty_for_string_only_clips():
    assert unmapped(["root_rotation", "pelvis_translation"], {}) == []
