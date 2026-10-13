# ERF Guide (.erf / .rim)

Flat resource archives. `.erf` and `.rim` share one format, told apart only by
extension. Three layouts ship in Dragon Age: Origins, identified by the first
16 bytes:

| magic                       | header | row  | name field          | payload                  | where                                  |
|-----------------------------|--------|------|---------------------|--------------------------|----------------------------------------|
| `"ERF V2.0"` UTF-16LE       | 32 B   | 72 B | 64 B UTF-16LE       | stored verbatim          | stock game, older repacks              |
| `"ERF V2.1"` ASCII + 8 zero | 32 B   | 44 B | 32 B ASCII          | zlib                     | `dao_prc_gib\module\env` (`.plo` planes) |
| `"ERF V2.2"` UTF-16LE       | 56 B   | 76 B | 64 B UTF-16LE       | Blowfish ECB, per entry  | DLC and promo modules (`AddIns`)       |

Implementation: [src/thedas_io/erf/description.py](../src/thedas_io/erf/description.py)
(`LAYOUTS`, `ERFReader`, `ERFWriter`), passwords in
[src/thedas_io/erf/passwords.py](../src/thedas_io/erf/passwords.py).

All integers are little-endian. Entry offsets are absolute file offsets. The
table of contents is `header + count * row`, and payloads follow it in table
order.

## V2.0

```
header 0-16   magic
       16-20  u32 entry_count
       20-28  unknown, preserve
       28-32  u32 sentinel 0xFFFFFFFF (checked on read)
entry  0-64   name, UTF-16LE, NUL-terminated, zero-padded (max 31 chars)
       64-68  u32 offset
       68-72  u32 size      (stored length; no separate packed length)
```

## V2.1

```
header 0-8    "ERF V2.1" ASCII, 8-16 zero
       16-20  u32 entry_count
       20-32  constants (identical in all 18 observed files), not checked
entry  0-32   name, ASCII, NUL-padded
       32-36  u32 offset
       36-40  u32 packed size   (zlib stream on disk)
       40-44  u32 size          (decompressed length)
```

Every observed entry zlib-decompresses to exactly its `size`.

## V2.2

```
header 0-16   magic
       16-20  u32 entry_count
       20-24  u32 year
       24-28  u32 day
       28-32  u32 unknown1 (-1 in observed files)
       32-36  u32 flags
              bits 4-7   encryption id (2 = Blowfish; 0 = none)
              bits 29-31 compression id (0 = none; the reader rejects others)
       36-40  u32 module_id
       40-56  md5 of the module password's decimal digits
entry  0-64   name, UTF-16LE, NUL-padded
       64-68  u32 offset
       68-72  u32 packed size   (multiple of 8 in every observed entry)
       72-76  u32 size          (plaintext length)
```

### Decrypting

1. Find the password: the first entry of `DAO_PASSWORDS` whose
   `md5(password.encode("ascii"))` equals header bytes 40-56. The stock list
   is the 24 published DAO module passwords. A module that uses a different
   password is reported with its `module_id`, while its entry names are still
   indexed.
2. Key: `struct.pack("<Q", int(password))`, 8 bytes little-endian.
3. Blowfish in ECB mode, with no IV, applied to the whole `packed size` bytes.
4. Keep the first `size` bytes. The rest is block padding, 0-7 bytes.

Names are not encrypted, so the table of contents can be read without any
password.

The reader does not validate encryption or compression until a payload is
requested. An unsupported archive still contributes its entries to the
database. In the tested DAO data every V2.2 file uses `encryption=2` and
`compression=0`.

## Verification

- `GFF V4.0 ... MMH` output for `ace_walkcol01_0.mmh` in
  `dao_prc_gib\module\data\modelhierarchies.erf` (password `13422531`, offset
  405704, packed 4224, size 4220) matches the output of pyGFF's `erfextract`
  byte for byte.
- All 12,990 `.msh`, `.mmh`, `.ani`, and `.plo` entries across the 3,323 AddIns
  archives decode to a `GFF ` header.
- The Blowfish primitive was checked against the Eric Young ECB test vectors.
