"""Append WriteBatch records to a LevelDB write-ahead log.

LevelDB replays every *.log file listed as live in the MANIFEST on open, so
appending a well-formed record to the current log is enough to inject new
keys without touching the SSTables or the MANIFEST. The batch sequence number
must be greater than every sequence already present in the DB, otherwise the
injected version loses the "newest wins" comparison.
"""
import os
import struct

from . import crc32c

BLOCK = 32768
FULL, FIRST, MIDDLE, LAST = 1, 2, 3, 4


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_batch(seq, entries):
    """entries: iterable of (key, value) puts; value None means delete."""
    entries = list(entries)
    buf = bytearray(struct.pack("<QI", seq, len(entries)))
    for key, value in entries:
        if value is None:
            buf.append(0)  # kTypeDeletion
            buf += _varint(len(key))
            buf += key
        else:
            buf.append(1)  # kTypeValue
            buf += _varint(len(key))
            buf += key
            buf += _varint(len(value))
            buf += value
    return bytes(buf)


def _record_fragments(payload, pos):
    """Split payload into log fragments respecting 32 KiB block boundaries."""
    out = []
    while payload:
        left = BLOCK - (pos % BLOCK)
        if left < 7:
            out.append((b"\x00" * left, None))  # block padding
            pos += left
            continue
        take = min(len(payload), left - 7)
        chunk, payload = payload[:take], payload[take:]
        out.append((chunk, pos))
        pos += 7 + take
    return out


def append_log(path, payload):
    """Append one WriteBatch payload as FULL/FIRST/MIDDLE/LAST records."""
    size = os.path.getsize(path)
    pos = size
    frags = []
    remaining = payload
    first = True
    while remaining or first:
        left = BLOCK - (pos % BLOCK)
        if left < 7:
            frags.append((b"\x00" * left, None))
            pos += left
            continue
        take = min(len(remaining), left - 7)
        chunk = remaining[:take]
        remaining = remaining[take:]
        if first and not remaining:
            rtype = FULL
        elif first:
            rtype = FIRST
        elif remaining:
            rtype = MIDDLE
        else:
            rtype = LAST
        frags.append((chunk, rtype))
        pos += 7 + take
        first = False
    with open(path, "ab") as fh:
        for chunk, rtype in frags:
            if rtype is None:
                fh.write(chunk)
                continue
            header = struct.pack("<IHB", 0, len(chunk), rtype)
            crc = crc32c.masked(header + chunk)
            fh.write(struct.pack("<IHB", crc, len(chunk), rtype))
            fh.write(chunk)
    return size


def max_seq(dbdir, reader_mod):
    """Highest sequence number currently present anywhere in the DB."""
    hi = 0
    tables, logs = reader_mod.db_files(dbdir)
    for path in tables:
        for _k, seq, _t, _v in reader_mod.read_table(path):
            if seq > hi:
                hi = seq
    for path in logs:
        for seq, _t, _k, _v in reader_mod.read_log(path):
            if seq > hi:
                hi = seq
    return hi


def current_log(dbdir):
    logs = [n for n in os.listdir(dbdir) if n.endswith(".log")]
    if not logs:
        raise FileNotFoundError("no .log file in %s" % dbdir)
    return os.path.join(dbdir, max(logs))
