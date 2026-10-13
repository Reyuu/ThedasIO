import hashlib
import struct
import zlib

from .passwords import DAO_PASSWORDS

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
    def __init__(self, name, offset, packed_size, size):
        self.name = name
        self.offset = offset
        # bytes on disk, which for an encrypted entry carries padding up to
        # the cipher block
        self.packed_size = packed_size
        # plaintext length, what the payload decodes to
        self.size = size


class ERFReader:
    def __init__(self, data):
        self.data = data
        self.entries = []
        self.encryption = 0
        self.compression = 0
        self.module_id = 0
        self.pw_hash = b""
        self.codec = None
        self._key = None
        self._parse()

    def _parse(self):
        header, row, name_size, encoding, self.codec = _layout(self.data[:16])
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
            # surrogate (the 64B field has no room for every resref plus a
            # NUL), and one odd byte would otherwise raise and lose the whole
            # archive from the scan.
            name = entry_data[:name_size].decode(encoding, "ignore").split("\x00", 1)[0]
            numbers = struct.unpack(f"<{(row - name_size) // 4}I", entry_data[name_size:])
            offset = numbers[0]
            # V2.0 records a single length; 2.1 and 2.2 record the packed and
            # the plaintext length separately.
            packed_size, size = (numbers[1], numbers[1]) if len(numbers) == 2 else numbers[1:3]
            self.entries.append(ERFEntry(name, offset, packed_size, size))

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

    def _find_key(self):
        # Blowfish keyed on the little-endian u64 of the module's numeric
        # password; the header only stores md5 of the decimal digits, so match
        # candidates against that rather than mapping module ids to passwords.
        for password in DAO_PASSWORDS:
            if hashlib.md5(password.encode("ascii")).digest() == self.pw_hash:
                return struct.pack("<Q", int(password))
        raise ValueError(f"no known password for ERF module {self.module_id}")

    def decode_payload(self, raw, entry):
        # Turn an entry's on-disk bytes into its plaintext.
        if self.codec == "zlib":
            return zlib.decompress(raw)
        if self.codec == "blowfish":
            # Raised here rather than in _parse so that an archive using
            # something we cannot read still contributes its entry names.
            if self.compression:
                raise ValueError(f"unsupported ERF compression {self.compression}")
            if self.encryption != 2:
                raise ValueError(f"unsupported ERF encryption {self.encryption}")
            from Crypto.Cipher import Blowfish

            return Blowfish.new(self.key, Blowfish.MODE_ECB).decrypt(raw)[: entry.size]
        return raw

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
