import argparse
import collections
import sys
from pathlib import Path

# Decodes every .msh under a game folder and reports what failed.
#
# Unit tests only cover the shapes we thought of. The index width was keyed off
# the index count rather than the serialized format, which broke thirteen shipped
# meshes (96795 indices over 22105 vertices) and no fixture in data/ looked like
# them; running this over a real install is what proved the fix. Point it at the
# DLC override folder, the base install, or Tools/daocmd/combined -- it should
# report zero failures.
#
# Also cross-checks the serialized index format (8032/8004) against the
# vertex-count heuristic, so the two rules cannot quietly diverge on a mod that
# ships a chunk we have never seen.
#
# Example run:
#   uv run .\tools\check_msh_corpus.py "F:\Modding\DAO\overridelink"


def check(root):
    from thedas_io.geometry import chunk_index_width, decode_chunk, index_width
    from thedas_io.msh.description import MSHReader

    files = chunks = 0
    failures = []
    formats = collections.Counter()
    disagree = []
    for path in sorted(Path(root).rglob("*.msh")):
        try:
            mesh = MSHReader(path.read_bytes()).mesh
        except Exception as e:  # noqa: BLE001 - the point is to report every bad file
            failures.append((path.name, "reader", f"{type(e).__name__}: {e}"))
            continue
        files += 1
        if not mesh.chunks:
            continue  # not a mesh node; 8032 here is a mis-rooted struct, not a format
        formats[mesh.index_format] += 1
        for chunk in mesh.chunks:
            chunks += 1
            try:
                decode_chunk(mesh, chunk)
            except Exception as e:  # noqa: BLE001 - same
                failures.append((path.name, chunk.name or "?", f"{type(e).__name__}: {e}"))
                continue
            # declared width vs what the vertex count alone would imply
            declared = chunk_index_width(chunk, mesh)
            implied = index_width(chunk.cells.get(8001, 0))
            if declared != implied:
                disagree.append(
                    (path.name, chunk.name or "?", chunk.cells.get(8001, 0), declared, implied)
                )

    return files, chunks, failures, formats, disagree


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="folder to walk for .msh files")
    args = ap.parse_args(argv)
    files, chunks, failures, formats, disagree = check(args.root)
    print(f"msh files {files}, chunks {chunks}, failures {len(failures)}")
    print(f"index_format: {dict(sorted(formats.items(), key=lambda kv: str(kv[0])))}")
    if disagree:
        print(f"declared format disagrees with the vertex count on {len(disagree)} chunks:")
        for name, chunk, verts, declared, implied in disagree[:20]:
            print(
                f"  {name} / {chunk}: {verts} verts, declared u{declared * 8}, implied u{implied * 8}"
            )
    for name, chunk, why in failures[:40]:
        print(f"  {name} / {chunk}: {why}")
    return 1 if failures or disagree else 0


if __name__ == "__main__":
    sys.exit(main())
