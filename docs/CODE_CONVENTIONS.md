## Code conventions

### File management

| Extension | File type | Comment |
| --------- | --------- | ------- |
| `.py`     | source    |         |
| `.toml`   | manifest / config | `blender_manifest.toml`, `pyproject.toml` |

Filenames for source modules should be unique across `src/` and `tests/` (e.g. `test_msh.py` tests `msh/`).

| Directory | Comment |
| --------- | ------- |
| `src/thedas_io/` | Add-on source. |
| `src/thedas_io/gff40/` | GFF V4.0 reader/writer, label table (`GFFReader`, `GFFStructDef`, `GFFField`). |
| `src/thedas_io/{msh,mmh,erf}/` | Format readers and writers. |
| `src/thedas_io/wheels/` | Vendored wheels shipped in the Blender bundle. Tracked. |
| `tests/` | Unit tests (`-m "not blender"`), real-Blender proofs (`-m blender`). |
| `tools/` | Dev scripts (`deploy.py`, `extract_labels.py`). |
| `data/` | Local sample files. Untracked, never commit. |
| `docs/` | Documentation about the project. |
| `build/` / `.venv/` | Generated. Should NEVER be committed. |

### Variable naming

Please do not use loaded terminology - avoid using terms that may have multiple interpretations or carry unintended connotations eg. "master", "slave", "dirty", "blacklist", "whitelist". If the term is unavoidable, it came from third-party code or established conventions, you're permitted to use it, but be aware of its implications.

### Formatting

Line length is 100, configured in `pyproject.toml`.

Before committing, run:

```sh
uv run ruff check src tests tools
```

### Binary parsing

All GFF V4.0 access goes through `GFFReader`. Do not scatter ad-hoc `struct.unpack` over header tables in callers. Do not confuse the two offset kinds, see `docs/GFF40_GUIDE.md`. `field_offset` points at definitions, `instance_base` at data.

Bad:

```python
name = LABELS[label_id]  # KeyError on the next sample with a new id
field = {f.label: f for f in fields}[label_id]  # silently drops phy d6j's second 6136
```

Good:

```python
name = LABELS.get(label_id, f"label_{label_id}")  # never drop unknowns
get_from_data_block(struct_ref, instance_base=0, label_id=label_id, occurrence=0)
```

### Roundtrip rules

- Every label id must survive a read/write cycle, even unnamed. Strict "all known" checks are opt-in only.
- Fields are stored sorted by label ascending, a writer must preserve that order.
- Gap bytes are `0xFF` fill when present. Derive length as `data_offset - fields_end`. Never assume alignment or non-emptiness. Preserve byte-exact on clone-then-patch writes.
- Do not hardcode struct-def counts or table positions. Walk, don't index. Root instance is at data-block offset 0.

### Labels

Ids cluster by domain (`6000-6345` hierarchy, `8000-8034` geometry, `6999` generic CHILDREN). Cross-file overlaps (`bnds` 8017-8019, `decl`/`vtdl` 8026-8031) pin shared semantics. Per-fourcc meaning still needs toolset knowledge. Until then, keep the numeric id as the key. The probe pattern that built the tables: `unpack_from("<IHHI")` over each struct's field block (see `tools/extract_labels.py`).
