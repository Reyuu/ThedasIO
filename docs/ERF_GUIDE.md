# ERF Guide (.erf, `ERF V2.0` console-script archive)

Flat file container: `[32B header][count x 72B entries][payloads]`. The magic is not NUL-terminated, bytes 16-19 are the entry count.

Implementation: [src/thedas_io/erf/description.py](src/thedas_io/erf/description.py)
(`ERFReader`, `ERFWriter`, `MAGIC`, `SENTINEL`).
- binary
- little-endian for ints
- names UTF-16LE
- payloads plain bytes (here ASCII CRLF console scripts).

No indexes, no compression, no nesting.

## Layout (byte-exact, verified)

```
[header 32B][entry-table 26*72B][data ...]
```

Header (32B):
```
0-16:  magic 8 WCHAR LE, no NUL: 45 00 52 00 46 00 20 00 56 00 32 00 2E 00 30 00
       = "ERF V2.0" (NOTE: the 0x1A byte at offset 16 is NOT part of the
       magic; it is the low byte of the count field below)
16-20: <I entry_count (= 26 here; (first_data_off-32)/72 == count)
20-24: <I unknown (observed 109 = 0x6D; meaning unverified, preserve it)
24-28: <I unknown (observed 203 = 0xCB; meaning unverified, preserve it)
28-32: <I sentinel 0xFFFFFFFF
```
Do not reinterpret the magic as NUL-terminated: bytes 16-19 are
`1A 00 00 00` = u32 26, the entry count. Earlier drafts misread `0x6D`
as a one-char string "m" — it is the u32 109.

Entry (72B, `entry i` at `32 + i*72`):
```
0-64:  name: 32 WCHAR LE = 64B, NUL-terminated, zero-padded.
       Max 31 chars + NUL. Observed: ASCII `*.cns` names.
64-68: <I offset: ABSOLUTE file offset of payload start
68-72: <I size:   payload length in BYTES (not WCHARs)
```
Table end = `32 + count*72` = first payload offset (1904 here). Payloads
are packed in table order; each offset is 4-byte aligned; gaps of 0-3
`0x00` bytes pad to the next boundary. Last payload end == file size
(51052 here). A zero-size entry is legal and shares its offset with the
next entry (`startup.cns` size 0 at 20280, same as `testscene.cns` start).

Payloads: raw bytes, `data[off:off+size]`. In this sample all are ASCII
`CRLF` scripts (`spawn ...\r\nclipplanes ...`). One observed body:
`animtest.cns` at 1904+222 = `spawn mb_world bob\r\nloadblend ...`.