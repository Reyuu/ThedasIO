"""Regenerate the precomputed DA2 tables from a DA2 install.

DA2 ERF V3 archives name their entries only by a 64-bit FNV-1 hash of the
lowercased path, so a scan can list an archive but not name most of what is in
it. This walks the install, collects every hash that the bundled wordlist
cannot name, and recovers as many as it can from the archives themselves.

Output is data, not source: ``src/thedas_io/asset_db/data/da2_names.bin`` and
``da2_type_exts.bin`` are zlib-compressed marshal tables of already-hashed
names, so the add-on never compiles ~340k literals or hashes the wordlist at
scan time. The wordlist itself lives in ``tools/data/da2_filenames.txt.gz``.

Usage:
    python tools/gen_da2_hashes.py --game "F:\\SteamLibrary\\steamapps\\common\\Dragon Age II"
"""

from __future__ import annotations

import argparse
import collections
import gzip
import marshal
import os
import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from thedas_io.asset_db.scanner import _mapped, _s64, fnv1
from thedas_io.erf.description import MAGIC_V3, ERFReader
from thedas_io.gff40.description import GFFReader

OUT_DIR = ROOT / "src" / "thedas_io" / "asset_db" / "data"
WORDLIST = ROOT / "tools" / "data" / "da2_filenames.txt.gz"


def load_wordlist() -> tuple[str, ...]:
    if not WORDLIST.is_file():
        return ()
    with gzip.open(WORDLIST, "rt", encoding="utf-8") as handle:
        return tuple(line.strip() for line in handle if line.strip())


DA2_FILENAMES = load_wordlist()
ARCHIVE_EXTS = {".erf", ".rim", ".crf", ".rimp"}
# every extension DA2 registers (fnvdump typelist)
DA2_EXTENSIONS = (
    "2da",
    "4pc",
    "als",
    "anb",
    "ani",
    "apl",
    "are",
    "arl",
    "art",
    "assembly",
    "bak",
    "bif",
    "bmp",
    "bnk",
    "btc",
    "btd",
    "bte",
    "btg",
    "bti",
    "btm",
    "btp",
    "bts",
    "btt",
    "bwm",
    "ccs",
    "cl",
    "cnv",
    "con",
    "cre",
    "css",
    "cur",
    "cut",
    "dat",
    "dds",
    "dft",
    "dwk",
    "dx11",
    "erf",
    "evt",
    "fac",
    "fdl",
    "fnt",
    "fx",
    "fxa",
    "fxe",
    "fxo",
    "gad",
    "gda",
    "gff",
    "gfx",
    "gic",
    "git",
    "hak",
    "ids",
    "ifo",
    "ini",
    "itp",
    "jrl",
    "key",
    "ldf",
    "lip",
    "log",
    "lst",
    "ltr",
    "lua",
    "lyt",
    "mao",
    "map",
    "mat",
    "mdl",
    "mdx",
    "mmh",
    "mml",
    "mod",
    "mop",
    "mor",
    "mp3",
    "mpg",
    "msh",
    "mtx",
    "mve",
    "ncc",
    "ncs",
    "ndb",
    "nss",
    "nwm",
    "ogg",
    "pck",
    "phy",
    "plh",
    "plo",
    "plt",
    "pth",
    "ptm",
    "ptt",
    "pwk",
    "res",
    "rim",
    "rml",
    "rsv",
    "s",
    "sav",
    "set",
    "sig",
    "slt",
    "ssf",
    "sta",
    "stg",
    "svr",
    "tex",
    "tga",
    "thg",
    "tlk",
    "tnt",
    "tpc",
    "ttf",
    "txb",
    "txi",
    "txt",
    "utc",
    "utd",
    "ute",
    "utg",
    "uti",
    "utm",
    "utp",
    "uts",
    "utt",
    "utw",
    "vis",
    "vlm",
    "wbd",
    "wav",
    "wfx",
    "wma",
    "wmv",
    "wok",
    "xbx",
    "xls",
    "xml",
    "xmv",
)
TEXT_SKIP = {".ogg", ".dds", ".vlm", ".msh", ".phy", ".ani"}
ASCII_RE = re.compile(rb"[A-Za-z0-9_\\/.\-]{3,120}")
UTF16_RE = re.compile(rb"(?:[A-Za-z0-9_\\/.\-]\x00){3,120}")
NAME_RE = re.compile(rb"[A-Za-z0-9_]{3,120}")
TEX_SUFFIX = ("_d", "_n", "_s", "_t", "_sa", "_e", "_h", "_0d", "_0n", "_0s", "_0t", "_0e", "_0h")
LIGHTMAP_PREFIXES = ("hlac_", "llac_", "hla_", "lla_")
GFF_NAME_LABELS = {".ani": 4006, ".gad": 4007}

# bruteforced entries
VERIFIED_EXTRAS = (
    "2d_travel_chateau.cut",
    "2p_100_lowangle_eye.stg",
    "2p_100_profile_eye.stg",
    "2p_200_faceaway90_eye.stg",
    "2p_200_lowangle_eye.stg",
    "2p_200_medium.stg",
    "area_intro.cut",
    "art\\characters\\creatures\\duke\\har_duka2.mao",
    "art\\characters\\playercharacter\\dwarfmale\\arm_hvyo1.mao",
    "art\\characters\\playercharacter\\elffemale\\ef_hed_tal_face.mao",
    "art\\characters\\playercharacter\\elffemale\\ef_hed_talp.mao",
    "art\\characters\\playercharacter\\humanmale\\glv_olta1.mao",
    "cd_drk000_larius.bnk",
    "cd_drk200_garev.bnk",
    "cd_eye500ar_mountainside.bnk",
    "cd_eye600ar_finale.bnk",
    "dlc_ingame_mageitempack.dds",
    "dlc_ingame_rogueitempack.dds",
    "dlc_ingame_warrioritempack.dds",
    "drk000_viewpoint_amb.bnk",
    "drk000_viewpoint_amb.cnv",
    "drk200st_carta_events.stg",
    "drk300_cs_trapped.bnk",
    "drk800st_janeka_confrontb.stg",
    "dukebust.cut",
    "eye300ip_incapacitate.ncs",
    "friday.ncs",
    "module_da2_prc_drk.dds",
    "module_da2_prc_eye.dds",
    "module_da2_prc_mxt001.dds",
    "module_da2_prc_mxt002.dds",
    "module_da2_prc_mxt003.dds",
    "rescuers_template.cut",
    "t3_bdy_olta.tnt",
    "t3_bdy_omdb.tnt",
    "t3_hlm_olta.tnt",
    "t3_hlm_omda.tnt",
    "t3_hlm_omdb.tnt",
    "tbe_module.ncs",
    "testbed.cut",
)


def fnv32(text: str) -> int:
    h = 2166136261
    for b in text.encode("ascii"):
        h = ((h * 16777619) & 0xFFFFFFFF) ^ b
    return h


PRIME_INV = pow(0x100000001B3, -1, 1 << 64)


def fnv1_inv(h: int, suffix: str) -> int:
    """Undo ``fnv1`` over a known trailing string, in place."""
    for b in reversed(suffix.encode()):
        h = ((h ^ b) * PRIME_INV) & 0xFFFFFFFFFFFFFFFF
    return h


def log(msg):
    print(msg, flush=True)


def find_archives(game: Path):
    for path in sorted(game.rglob("*")):
        if path.is_file() and path.suffix.lower() in ARCHIVE_EXTS:
            yield path


def unresolved_hashes(archives, type_exts):
    known = {_s64(fnv1(fn.lower())) for fn in DA2_FILENAMES}
    ext_of, packages, entries = {}, collections.defaultdict(set), collections.defaultdict(list)
    for path in archives:
        try:
            with _mapped(path) as data:
                if data[:16] != MAGIC_V3:
                    continue
                reader = ERFReader(data)
                for entry in reader.entries:
                    if entry.name:
                        continue
                    sh = _s64(entry.name_hash)
                    if sh in known:
                        continue
                    ext = type_exts.get(entry.type_hash, ".bin")
                    ext_of[sh] = ext
                    packages[sh].add(path.name.lower())
                    entries[path].append((sh, ext, entry.offset, entry.packed_size))
        except (OSError, ValueError, struct.error):
            continue
    return ext_of, entries


def recover(archives, type_exts):
    ext_of, entries = unresolved_hashes(archives, type_exts)
    log(f"unresolved distinct hashes: {len(ext_of)}")
    pending = set(ext_of)
    found = {}

    def offer(name):
        h = _s64(fnv1(name))
        if h in pending and h not in found:
            found[h] = name
            return True
        return False

    # 1. Wwise media ids: '<u32>.ogg'
    ogg = {h for h, ext in ext_of.items() if ext == ".ogg"}
    for path in archives:
        low = path.name.lower()
        if "wwisebanks" not in low:
            continue
        try:
            with _mapped(path) as data:
                reader = ERFReader(data)
                values = set()
                for entry in reader.entries:
                    plain = reader.decode_payload(
                        data[entry.offset : entry.offset + entry.packed_size], entry
                    )
                    for off in range(4):
                        chunk = plain[off : off + ((len(plain) - off) // 4) * 4]
                        values.update(v for (v,) in struct.iter_unpack("<I", chunk))
            for v in values:
                offer(f"{v}.ogg")
        except (OSError, ValueError, struct.error):
            continue
    log(f"  .ogg: {len([h for h in found if ext_of[h] == '.ogg'])}/{len(ogg)}")

    # 2. lightmaps and level stems
    level_stems, gpu_stems = set(), set()
    for path in archives:
        low = path.name.lower()
        if low.endswith(".gpu.rim"):
            stem = low[:-8]
            gpu_stems.add(stem)
            if "_" in stem:
                level_stems.add(stem.rsplit("_", 1)[0])
        elif low.endswith(".rim"):
            level_stems.add(low[:-4])
    for stem in gpu_stems:
        offer(f"{stem}.rml")
        for prefix in LIGHTMAP_PREFIXES:
            for idx in range(250):
                for suffix in ("", "x", "y", "z", "_0", "_1"):
                    offer(f"{prefix}{stem}_{idx}{suffix}.dds")
    for stem in level_stems:
        for suffix in ("_mmi.gff", ".rml", "_0_0.rml", "_mmap.dds", "_mmap_ph.dds"):
            offer(f"{stem}{suffix}")
    log(f"  +lightmaps -> {len(found)}")

    # 3. string scrape of every non-base-game archive (the DLC data is what the
    #    bundled wordlists predate)
    strings, seen = set(), set()
    for path in archives:
        low = str(path).lower()
        if (
            "addins" not in low
            and "patch" not in low
            and path.suffix.lower() not in (".crf", ".rimp")
        ):
            continue
        key = (path.name.lower(), path.stat().st_size)
        if "wwise" in key[0] or ".gpu.rim" in key[0] or key in seen:
            continue
        seen.add(key)
        try:
            with _mapped(path) as data:
                if data[:16] != MAGIC_V3:
                    continue
                reader = ERFReader(data)
                for entry in reader.entries:
                    if type_exts.get(entry.type_hash, "") in TEXT_SKIP:
                        continue
                    try:
                        plain = reader.decode_payload(
                            data[entry.offset : entry.offset + entry.packed_size], entry
                        )
                    except (OSError, ValueError, KeyError, IndexError, struct.error):
                        continue
                    strings.update(
                        m.group(0).decode("ascii", "ignore").lower()
                        for m in ASCII_RE.finditer(plain)
                    )
                    strings.update(
                        m.group(0).decode("utf-16le", "ignore").lower()
                        for m in UTF16_RE.finditer(plain)
                    )
        except (OSError, ValueError, struct.error):
            continue
    log(f"  scraped {len(strings)} strings from {len(seen)} archives")
    exts = tuple(set(ext_of.values()) - {".ogg"})
    for text in strings:
        text = text.strip("\x00").replace("/", "\\")
        if not text:
            continue
        candidates = [text, text + "x.dds", text + "y.dds", text + "z.dds"]
        candidates.extend(text + ext for ext in exts)
        low = text.lower()
        if low.startswith("hlac_"):
            candidates.extend("llac_" + text[5:] + s for s in (".dds", "x.dds", "y.dds", "z.dds"))
        elif low.startswith("hla_"):
            candidates.extend("lla_" + text[4:] + s for s in (".dds", "x.dds", "y.dds", "z.dds"))
        if "." in text:
            stem = text.rsplit(".", 1)[0]
            candidates.extend(stem + ext for ext in exts)
            if low.endswith(".dds"):
                base = text[:-4]
                for suffix in TEX_SUFFIX:
                    if base.endswith(suffix):
                        root = base[: -len(suffix)]
                        candidates.extend(root + other + ".dds" for other in TEX_SUFFIX)
        for candidate in candidates:
            offer(candidate)
    log(f"  +strings -> {len(found)}")

    # 3b. naming conventions over that vocabulary: '<stem><ext>',
    #     '<stem>_<n><ext>' and '<stem>_<a>_<b><ext>'. Stems keep their full
    #     path when the scrape saw one, so path-qualified names fall out too.
    stems = set()
    dirs = {fn.lower().rsplit("\\", 1)[0] for fn in DA2_FILENAMES if "\\" in fn}
    for text in strings:
        text = text.strip("\x00").replace("/", "\\")
        if not text:
            continue
        stems.add(text)
        stems.add(text.rsplit("\\", 1)[-1])
        if "." in text:
            stems.add(text.rsplit(".", 1)[0])
        parts = text.split("\\")
        for i in range(1, len(parts)):
            dirs.add("\\".join(parts[:i]))
    for name in DA2_FILENAMES:
        stems.add(name.lower())
        stems.add(name.lower().rsplit(".", 1)[0])
    stems.discard("")
    log(f"  {len(stems)} stems, {len(dirs)} directories")

    bare = {_s64(fnv1(s)): s for s in stems}
    under = {_s64(fnv1(s + "_")): s for s in stems}
    indices = tuple(str(i) for i in range(256))
    for h, ext in tuple(ext_of.items()):
        if h in found:
            continue
        tail = fnv1_inv(h & 0xFFFFFFFFFFFFFFFF, ext)
        stem = bare.get(_s64(tail))
        if stem is not None:
            found[h] = stem + ext
            continue
        for n in indices:
            stem = under.get(_s64(fnv1_inv(tail, n)))
            if stem is not None:
                found[h] = f"{stem}_{n}{ext}"
                break
        if h in found:
            continue
        for b in range(10):
            middle = fnv1_inv(fnv1_inv(tail, str(b)), "_")
            for a in indices:
                stem = under.get(_s64(fnv1_inv(middle, a)))
                if stem is not None:
                    found[h] = f"{stem}_{a}_{b}{ext}"
                    break
            if h in found:
                break
    log(f"  +families -> {len(found)}")

    # 4. self-describing payloads: .ani/.gad animation names and .ncs script
    #    names; the directory is resolved by inverting the name out of the hash
    #    and looking the remaining state up among every scraped directory.
    dstate = {_s64(fnv1(d + "\\")): d for d in dirs | {""}}

    def offer_leaf_for(target: int, leaf: str) -> str | None:
        """Resolve '<dir>\\<leaf>' or '<leaf>' for one pending hash."""
        if _s64(fnv1(leaf)) == target:
            if target in pending and target not in found:
                found[target] = leaf
                return ""
            return None
        state = _s64(fnv1_inv(target & 0xFFFFFFFFFFFFFFFF, leaf))
        directory = dstate.get(state)
        if directory is None:
            return None
        name = leaf if directory == "" else f"{directory}\\{leaf}"
        if target in pending and target not in found:
            found[target] = name
            return directory
        return None

    for path, items in entries.items():
        wanted = {(sh, ext) for sh, ext, _, _ in items if ext in (".ani", ".gad", ".ncs")}
        if not wanted:
            continue
        try:
            with _mapped(path) as data:
                reader = ERFReader(data)
                for entry in reader.entries:
                    sh = _s64(entry.name_hash)
                    if sh not in ext_of or ext_of[sh] not in (".ani", ".gad", ".ncs"):
                        continue
                    try:
                        plain = reader.decode_payload(
                            data[entry.offset : entry.offset + entry.packed_size], entry
                        )
                    except (OSError, ValueError, KeyError, IndexError, struct.error):
                        continue
                    ext = ext_of[sh]
                    leaves = []
                    label = GFF_NAME_LABELS.get(ext)
                    if label and plain[:3] == b"GFF":
                        try:
                            value = GFFReader(plain).get_from_data_block(0).get(label)
                        except (OSError, ValueError, KeyError, IndexError, struct.error):
                            value = None
                        if isinstance(value, str) and value.strip():
                            value = value.lower().strip()
                            leaves.append(value if value.endswith(ext) else value + ext)
                    elif ext == ".ncs":
                        leaves.extend(
                            s + ".ncs"
                            for s in (
                                m.group(0).decode("ascii").lower() for m in NAME_RE.finditer(plain)
                            )
                            if s != "ncs"
                        )
                    for leaf in leaves:
                        directory = offer_leaf_for(sh, leaf)
                        if directory is None:
                            continue
                        # the .gad/.evt siblings share the stem and directory
                        stem = leaf.rsplit(".", 1)[0]
                        for sibling in (".gad", ".evt"):
                            if not leaf.endswith(sibling):
                                offer(stem + sibling)
                                if directory:
                                    offer(f"{directory}\\{stem}{sibling}")
                        break
        except (OSError, ValueError, struct.error):
            continue
    log(f"  +names -> {len(found)}")

    # 5. pathfinding patches: '<area>.pwk', bare or under a known directory
    before = len(found)
    for stem in level_stems:
        for leaf in (f"{stem}.pwk", f"{stem}_p.pwk"):
            offer(leaf)
            for directory in dirs:
                offer(f"{directory}\\{leaf}")
    log(f"  +pathfinding -> +{len(found) - before}")

    # 6. names from the offline meet-in-the-middle pass; see VERIFIED_EXTRAS
    before = len(found)
    for name in VERIFIED_EXTRAS:
        offer(name)
    log(f"  +verified extras -> +{len(found) - before}")
    return ext_of, found


def _dump(path: Path, table: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = zlib.compress(marshal.dumps(table, 4), 9)
    path.write_bytes(blob)
    log(f"wrote {len(table)} entries to {path.name} ({len(blob)} bytes)")


def write_tables(type_exts, found):
    # the wordlist wins over names recovered offline, matching the scanner's
    # order; an exact 64-bit hash then supplies both the name and its extension
    names = {}
    for fn in DA2_FILENAMES:
        names.setdefault(_s64(fnv1(fn.lower())), os.path.basename(fn))
    for h in sorted(found):
        names.setdefault(h, found[h].replace("\\", "/").rsplit("/", 1)[-1])
    _dump(OUT_DIR / "da2_names.bin", {h: names[h] for h in sorted(names)})
    _dump(OUT_DIR / "da2_type_exts.bin", dict(sorted(type_exts.items())))


def write_filelist(found, path, extras=()):
    """Write a pyGFF "Import Strings -> Filenames" list."""
    names = {n.lower() for n in found.values()}
    for extra in extras:
        with open(extra, encoding="utf-8", errors="replace") as handle:
            names.update(
                line.strip().lower()
                for line in handle
                if "." in line and line.strip()
            )
    by_ext = collections.defaultdict(set)
    for name in names:
        by_ext[name.rsplit(".", 1)[-1].lower()].add(name)
    lines = []
    for ext in sorted(by_ext):
        lines.append(ext.upper())
        lines.extend(sorted(by_ext[ext]))
    Path(path).write_bytes("\r\n".join(lines).encode("utf-8"))
    log(f"wrote filelist {sum(len(v) for v in by_ext.values())} names in "
        f"{len(by_ext)} extensions to {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game",
        default=os.environ.get("DA2_PATH", r"F:\SteamLibrary\steamapps\common\Dragon Age II"),
        help="Dragon Age II install directory",
    )
    parser.add_argument(
        "--filelist",
        help="also write a pyGFF filename list to this path (full paths kept)",
    )
    parser.add_argument(
        "--extra-filelist",
        action="append",
        default=[],
        help="merge another tool's filename list into --filelist (repeatable)",
    )
    args = parser.parse_args(argv)
    game = Path(args.game)
    if not game.is_dir():
        parser.error(f"not a directory: {game}")

    type_exts = {fnv32(t): f".{t}" for t in DA2_EXTENSIONS}

    archives = list(find_archives(game))
    log(f"{len(archives)} archives under {game}")
    ext_of, found = recover(archives, type_exts)
    write_tables(type_exts, found)
    if args.filelist:
        write_filelist(found, args.filelist, args.extra_filelist)
    log(f"still unnamed: {len(ext_of) - len(found)} distinct hashes")
    by_ext = collections.Counter(ext_of[h] for h in ext_of if h not in found)
    log(f"  by ext: {by_ext.most_common(12)}")


if __name__ == "__main__":
    main()
