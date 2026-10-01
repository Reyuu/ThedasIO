import struct

# erf v2.0: [header 32B][entries n*72B][payloads]
# header: magic "ERF V2.0" as 8 wchar, entry_count, 2x unknown, sentinel
# entry: name as 32 wchar NUL-padded, <I offset, <I size
# first payload at 32 + n*72

MAGIC = "ERF V2.0".encode("utf-16-le")
SENTINEL = 0xFFFFFFFF


class ERFEntry:
    def __init__(self, name, offset, size):
        self.name = name
        self.offset = offset
        self.size = size


class ERFReader:
    def __init__(self, data):
        self.data = data
        self.entries = []
        self._parse()

    def _parse(self):
        magic = self.data[0:16]
        if magic != MAGIC:
            raise ValueError("Invalid ERF file magic")
        entry_count, _u1, _u2, sentinel = struct.unpack("<IIII", self.data[16:32])
        if sentinel != SENTINEL:
            raise ValueError("Invalid ERF file sentinel")

        for i in range(entry_count):
            entry_offset = 32 + i * 72
            entry_data = self.data[entry_offset : entry_offset + 72]
            name_bytes = entry_data[0:64]
            name = name_bytes.decode("utf-16le").split("\x00", 1)[0]
            offset, size = struct.unpack("<II", entry_data[64:72])
            self.entries.append(ERFEntry(name, offset, size))

    def get_payload(self, entry_name):
        for entry in self.entries:
            if entry.name == entry_name:
                return self.data[entry.offset : entry.offset + entry.size]
        raise KeyError(f"Entry '{entry_name}' not found")


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
