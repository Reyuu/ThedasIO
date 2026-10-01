## Dev workflow

### Prerequisites

- `uv`, Python 3.11+
- Blender 5.2 (only needed for deploy + real-Blender proofs)
- DAO game folder, set once in the add-on preferences. `Scan` indexes it into a SQLite asset database

### Setup

Clone the repo:

```sh
git clone <this repo>
```

### Testing

```sh
uv run pytest -m "not blender"   # fast unit tests, no Blender needed
uv run pytest                    # adds the real-Blender proofs (~2.5 min)
uv run ruff check src tests tools
```

Real-Blender tests spawn `blender --background` and are marked `blender`. The executable resolves from `BLENDER_EXE`, then `PATH`, then the default install location.

### Deploying

```sh
uv run tools/deploy.py
```

Copies `src/thedas_io/` (manifest, code, wheels) into Blender's user extensions folder. Then enable *ThedasIO* in Preferences → Add-ons.

### Oneliner

```sh
uv run ruff check src tests tools && uv run pytest -m "not blender"
```

### Type checking

mypy needs a 3.13 host to parse the Blender stubs; the dev env runs older. Run it isolated:

```sh
uv run --no-project --python 3.13 --with mypy --with pillow --with "numpy>=2.4.6" --with "pynvtt>=0.0.2" mypy src
```

### Troubleshooting

If the add-on does not appear after deploy, check the Blender user extensions folder for Blender 5.2, and enable the add-on in Preferences.

If real-Blender tests cannot find Blender, set `BLENDER_EXE` explicitly.

### Known issues

- Full `pytest` takes ~2.5 min and requires a real Blender. Use `-m "not blender"` while iterating.
