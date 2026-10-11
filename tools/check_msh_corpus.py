import argparse
import sys
from pathlib import Path

# Decodes every .msh under a game folder and reports what failed.
#
# Unit tests only cover the shapes we thought of. The index-width rule in
# geometry.index_width was wrong for thirteen shipped meshes (96795 indices over
# 22105 vertices) and no fixture in data/ looked like them; running this over a
# real install is what proved the fix. Point it at the DLC override folder or the
# base install and it should report zero failures.
#
# Example run:
#   uv run .\tools\check_msh_corpus.py "F:\Modding\DAO\overridelink"


def check(root):
    from thedas_io.geometry import decode_chunk
    from thedas_io.msh.description import MSHReader

    files = chunks = 0
    failures = []
    for path in sorted(Path(root).rglob("*.msh")):
        try:
            mesh = MSHReader(path.read_bytes()).mesh
        except Exception as e:  # noqa: BLE001 - the point is to report every bad file
            failures.append((path.name, "reader", f"{type(e).__name__}: {e}"))
            continue
        files += 1
        for chunk in mesh.chunks:
            chunks += 1
            try:
                decode_chunk(mesh, chunk)
            except Exception as e:  # noqa: BLE001 - same
                failures.append((path.name, chunk.name or "?", f"{type(e).__name__}: {e}"))

    return files, chunks, failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="folder to walk for .msh files")
    args = ap.parse_args(argv)

    files, chunks, failures = check(args.root)
    print(f"msh files {files}, chunks {chunks}, failures {len(failures)}")
    for name, chunk, why in failures[:40]:
        print(f"  {name} / {chunk}: {why}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
