# DA2 names a channel by a u32 id, not the mmh export name. no shipped hash
# reproduces that id and no asset carries the table, so the raw id is the
# channel's name: a clip drives a bone only when the bone carries the id or a
# da2_channels.json entry maps it.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TABLE_NAME = "da2_channels.json"


def table_path() -> Path:
    # the table shipped beside this module
    return Path(__file__).with_name(TABLE_NAME)


def load_table(path: Any = None) -> dict:
    # id as text -> export name; a missing or malformed file maps nothing
    target = Path(path) if path is not None else table_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if key and value}


def resolve(name: Any, table: dict) -> str:
    # the export name behind a channel name, or the name itself (DAO strings)
    return table.get(str(name), str(name))


def unmapped(names: Any, table: dict) -> list:
    # numeric ids with no entry, first-seen order
    out: list = []
    for name in names:
        key = str(name)
        if key.isdigit() and key not in table and key not in out:
            out.append(key)
    return out
