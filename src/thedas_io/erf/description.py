import hashlib
import struct
import uuid
import zlib

from .passwords import DA2_PASSWORDS, DAO_PASSWORDS

# Plaintext magic prefixes a successful Blowfish decryption should start with.
# DA2 DLC archives encrypt GFF/DDS/UTI/etc. payloads; wrong keys yield garbage.
_PAYLOAD_MAGICS = (
    b"GFF ",
    b"DDS ",
    b"UTI ",
    b"UTP ",
    b"UTC ",
    b"NCS ",
    b"PLO ",
    b"BIOE",
    b"GDA ",
)

# Three container layouts ship in the wild, told apart by the first 16 bytes:
#
#   magic                header  row   name field         payload
#   "ERF V2.0" utf-16le     32B   72B   64B utf-16le       verbatim
#   "ERF V2.1" ascii + 0s   32B   44B   32B ascii         zlib
#   "ERF V2.2" utf-16le     56B   76B   64B utf-16le       Blowfish ECB
#
# V2.1 is the odd one out: ascii rather than utf-16 throughout, and zlib
# rather than encryption. It only turns up in dao_prc_gib's module\env areas,
# where the contents are .plo planes files.

MAGIC = "ERF V2.0".encode("utf-16-le")
MAGIC_21 = b"ERF V2.1" + b"\x00" * 8
MAGIC_22 = "ERF V2.2".encode("utf-16-le")
MAGIC_V3 = "ERF V3.0".encode("utf-16-le")
SENTINEL = 0xFFFFFFFF

# 16-byte prefix -> (header size, row size, name field size, name encoding, codec)
LAYOUTS = {
    MAGIC: (32, 72, 64, "utf-16-le", None),
    MAGIC_21: (32, 44, 32, "ascii", "zlib"),
    MAGIC_22: (56, 76, 64, "utf-16-le", "blowfish"),
}


def _layout(prefix):
    try:
        return LAYOUTS[prefix]
    except KeyError:
        raise ValueError("Invalid ERF file magic") from None


def geometry(prefix):
    # (header size, row size) for a 16-byte file prefix.
    header, row, _name_size, _encoding, _codec = _layout(prefix)
    return header, row


class ERFEntry:
    def __init__(self, name, offset, packed_size, size, name_hash=0, type_hash=0):
        self.name = name
        self.offset = offset
        # bytes on disk, which for an encrypted entry carries padding up to
        # the cipher block
        self.packed_size = packed_size
        # plaintext length, what the payload decodes to
        self.size = size
        self.name_hash = name_hash
        self.type_hash = type_hash


class ERFReader:
    def __init__(self, data):
        self.data = data
        self.entries = []
        self.encryption = 0
        self.compression = 0
        self.module_id = 0
        self.pw_hash = b""
        self.codec = None
        self.string_table = b""
        self._key = None
        self._parse()

    def _parse(self):
        prefix = self.data[:16]
        if prefix == MAGIC_V3:
            self._parse_v3()
            return

        header, row, name_size, encoding, self.codec = _layout(prefix)
        entry_count = struct.unpack("<I", self.data[16:20])[0]
        if self.codec == "blowfish":
            self._parse_v22_header()
        # V2.0 is the only layout with a sentinel; V2.1 puts version constants
        # in that slot instead
        elif self.codec is None and struct.unpack("<I", self.data[28:32])[0] != SENTINEL:
            raise ValueError("Invalid ERF file sentinel")

        for i in range(entry_count):
            start = header + i * row
            entry_data = self.data[start : start + row]
            # errors="ignore" is load-bearing: a name field can end mid
            # surrogate (the 64B field has no room for every resref plus a NUL),
            # and one odd byte would otherwise raise and lose the whole
            # archive from the scan.
            name = entry_data[:name_size].decode(encoding, "ignore").split("\x00", 1)[0]
            numbers = struct.unpack(f"<{(row - name_size) // 4}I", entry_data[name_size:])
            offset = numbers[0]
            # V2.0 records a single length; 2.1 and 2.2 record the packed and
            # the plaintext length separately.
            packed_size, size = (numbers[1], numbers[1]) if len(numbers) == 2 else numbers[1:3]
            self.entries.append(ERFEntry(name, offset, packed_size, size))

        if self.codec == "zlib":
            self.compression = 1
        if self.codec == "blowfish":
            # V2.2 header already parsed; encryption/compression from flags
            pass

    # nightmare, nightmare, nightmare
    def _parse_v3(self):
        if len(self.data) < 48:
            raise ValueError("Invalid ERF V3.0 header")
        string_table_size, file_count, flags = struct.unpack("<III", self.data[16:28])
        self.module_id = struct.unpack("<I", self.data[28:32])[0]
        self.pw_hash = self.data[32:48]
        self.encryption = (flags >> 4) & 0xF
        self.compression = (flags >> 29) & 0x7
        if 48 + string_table_size > len(self.data):
            raise ValueError("Invalid ERF V3.0 string table")
        self.string_table = self.data[48 : 48 + string_table_size]
        toc_offset = 48 + string_table_size
        row_size = 28
        if toc_offset + file_count * row_size > len(self.data):
            raise ValueError("Invalid ERF V3.0 table of contents")
        for i in range(file_count):
            start = toc_offset + i * row_size
            entry_data = self.data[start : start + row_size]
            name_offset, name_hash, type_hash, offset, packed_size, size = struct.unpack(
                "<iQIIII", entry_data
            )
            name = ""
            if name_offset >= 0 and name_offset < len(self.string_table):
                end = self.string_table.find(b"\x00", name_offset)
                if end < 0:
                    end = len(self.string_table)
                name = self.string_table[name_offset:end].decode("ascii", "replace")
            self.entries.append(
                ERFEntry(name, offset, packed_size, size, name_hash=name_hash, type_hash=type_hash)
            )

    def _parse_v22_header(self):
        # V2.2 spends the 56-byte header on a flags word, the module id and
        # the password digest where V2.0 has just a sentinel.
        _, _, _, _, flags, module_id = struct.unpack("<6I", self.data[16:40])
        self.module_id = module_id
        self.pw_hash = self.data[40:56]
        self.encryption = (flags >> 4) & 0xF
        self.compression = (flags >> 29) & 0x7

    @property
    def key(self):
        # Resolved on first use, not at parse time: entry names are not
        # encrypted, so a scan still indexes an archive whose module we hold
        # no password for, and only the extraction is reported as failing.
        if self._key is None:
            self._key = self._find_key()
        return self._key

    def _first_packed(self) -> bytes | None:
        # raw packed bytes of the first entry, used to verify a DA2 key guess
        for entry in self.entries:
            raw = self.data[entry.offset : entry.offset + min(entry.packed_size, 64)]
            if raw:
                return raw
        return None

    def _da2_key(self) -> bytes | None:
        # DA2 keys UUID passwords with the raw UUID bytes as the Blowfish key
        # Encryption-3 headers store md5(Blowfish(key).encrypt(bytes(range(256))));
        # check that first so compressed or headerless entries still resolve,
        # and keep the magic check as a fallback for synthetic test archives
        from Crypto.Cipher import Blowfish

        probe = bytes(range(256))
        keys = []
        for password in DA2_PASSWORDS:
            try:
                key = uuid.UUID(password).bytes
            except ValueError:
                continue
            keys.append(key)
            if hashlib.md5(Blowfish.new(key, Blowfish.MODE_ECB).encrypt(probe)).digest() == self.pw_hash:
                return key

        packed = self._first_packed()
        if packed is None:
            return None
        for key in keys:
            try:
                out = Blowfish.new(key, Blowfish.MODE_ECB).decrypt(packed)
            except (ValueError, TypeError):
                continue
            if out[:4] in _PAYLOAD_MAGICS or out[:8] == b"GFF V4.0":
                return key
        return None

    def _find_key(self):
        # Blowfish keyed on the little-endian u64 of the module's numeric
        # password; the header only stores md5 of the decimal digits, so match
        # candidates against that rather than mapping module ids to passwords.
        for password in DAO_PASSWORDS:
            if hashlib.md5(password.encode("ascii")).digest() == self.pw_hash:
                return struct.pack("<Q", int(password))
        # DA2 DLC modules: raw UUID bytes are the key
        key = self._da2_key()
        if key is not None:
            return key
        raise ValueError(f"no known password for ERF module {self.module_id}")

    def decode_payload(self, raw, entry):
        # Turn an entry's on-disk bytes into its plaintext.
        if self.codec == "zlib":
            return zlib.decompress(raw)
        # V3 (and flag-driven) path: encryption and compression are independent(!)
        data = raw
        if self.encryption:
            if self.encryption in (2, 3):
                from Crypto.Cipher import Blowfish

                data = Blowfish.new(self.key, Blowfish.MODE_ECB).decrypt(data)
            elif self.encryption == 1:
                key = self._xor_key()
                out = bytearray()
                for i, b in enumerate(data):
                    out.append(b ^ key[i % len(key)])
                data = bytes(out)
            else:
                raise ValueError(f"unsupported ERF encryption {self.encryption}")

        if self.compression:
            if self.compression == 1:
                # Bioware zlib: 1-byte header (window bits in high nibble), then raw deflate
                data = zlib.decompress(data[1:], wbits=-15)
            elif self.compression == 7:
                data = zlib.decompress(data, wbits=-15)
            else:
                raise ValueError(f"unsupported ERF compression {self.compression}")

        if entry is not None and getattr(entry, "size", 0):
            data = data[: entry.size]
        return data

    def _xor_key(self):
        # 9-byte key derived from the module password
        password = ""
        try:
            # reuse the blowfish password match by digest
            password = self._matched_password()
        except Exception:  # noqa: BLE001
            password = ""
        value = int(password) if password.isdigit() else 0
        key = value.to_bytes(9, "little")
        return key

    def _matched_password(self):
        for password in DAO_PASSWORDS:
            import hashlib

            if hashlib.md5(password.encode("ascii")).digest() == self.pw_hash:
                return password
        return ""

    def entry(self, entry_name):
        for entry in self.entries:
            if entry.name == entry_name:
                return entry
        raise KeyError(f"Entry '{entry_name}' not found")

    def get_payload(self, entry_name):
        entry = self.entry(entry_name)
        return self.decode_payload(
            self.data[entry.offset : entry.offset + entry.packed_size], entry
        )


class ERFWriter:
    def __init__(self):
        self.entries = []
        self.payloads = []

    def add_entry(self, name, payload):
        if len(name) > 31:
            raise ValueError("Entry name must be at most 31 characters")
        self.entries.append((name, payload))

    def write(self):
        entry_count = len(self.entries)
        header = MAGIC + struct.pack("<IIII", entry_count, 0, 0, SENTINEL)

        entry_data = b""
        payload_data = b""
        current_offset = 32 + entry_count * 72

        for name, payload in self.entries:
            name_bytes = name.encode("utf-16le") + b"\x00\x00"
            name_bytes = name_bytes.ljust(64, b"\x00")
            size = len(payload)
            entry_data += name_bytes + struct.pack("<II", current_offset, size)
            payload_data += payload
            current_offset += size

        return header + entry_data + payload_data
