"""Minimal LevelDB reader: SSTable (.ldb/.sst) + write-ahead log (.log).

Only what we need: enumerate every live user key/value in a DB directory,
honouring sequence numbers so the newest version of a key wins and deletions
are respected.
"""
import os
import struct

from . import snappy

TABLE_MAGIC = 0xDB4775248B80FB57
FOOTER_SIZE = 48


def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _read_block(fh, offset, size):
    # LevelDB BlockHandle.size covers the block body only; the 5-byte
    # trailer (compression type + crc32) follows it in the file.
    fh.seek(offset)
    content = fh.read(size)
    if len(content) != size:
        raise ValueError("short block read at %d" % offset)
    trailer = fh.read(5)
    if len(trailer) < 5:
        raise ValueError("short block trailer at %d" % offset)
    ctype = trailer[0]
    if ctype == 0:
        return content
    if ctype == 1:
        return snappy.decompress(content)
    raise ValueError("unsupported block compression type %d" % ctype)


def _iter_block(content):
    """Yield (key, value) from an uncompressed block body."""
    if len(content) < 4:
        return
    num_restarts = struct.unpack_from("<I", content, len(content) - 4)[0]
    end = len(content) - 4 - 4 * num_restarts
    pos = 0
    key = b""
    while pos < end:
        shared, pos = _varint(content, pos)
        non_shared, pos = _varint(content, pos)
        vlen, pos = _varint(content, pos)
        key = key[:shared] + content[pos:pos + non_shared]
        pos += non_shared
        value = content[pos:pos + vlen]
        pos += vlen
        yield key, value


def _parse_internal_key(ikey):
    """internal key = user_key + 8 bytes (seq<<8 | type)."""
    if len(ikey) < 8:
        raise ValueError("internal key too short")
    user_key = ikey[:-8]
    tag = struct.unpack_from("<Q", ikey, len(ikey) - 8)[0]
    return user_key, tag >> 8, tag & 0xFF  # type 1 = value, 0 = deletion


def read_table(path):
    """Yield (user_key, seq, type, value) for every entry in one SSTable."""
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size < FOOTER_SIZE:
            return
        fh.seek(size - FOOTER_SIZE)
        footer = fh.read(FOOTER_SIZE)
        magic = struct.unpack_from("<Q", footer, FOOTER_SIZE - 8)[0]
        if magic != TABLE_MAGIC:
            raise ValueError("%s: bad table magic %x" % (path, magic))
        pos = 0
        _mo, pos = _varint(footer, pos)
        _ms, pos = _varint(footer, pos)
        io, pos = _varint(footer, pos)
        isz, pos = _varint(footer, pos)
        index = _read_block(fh, io, isz)
        handles = []
        for _k, v in _iter_block(index):
            p = 0
            off, p = _varint(v, p)
            sz, p = _varint(v, p)
            handles.append((off, sz))
        for off, sz in handles:
            block = _read_block(fh, off, sz)
            for ikey, value in _iter_block(block):
                user_key, seq, typ = _parse_internal_key(ikey)
                yield user_key, seq, typ, value


def _log_records(path):
    """Yield reassembled write-batch payloads from a LevelDB .log file."""
    with open(path, "rb") as fh:
        data = fh.read()
    BLOCK = 32768
    pos = 0
    pending = b""
    while pos < len(data):
        block_left = BLOCK - (pos % BLOCK)
        if block_left < 7:  # block trailer padding
            pos += block_left
            continue
        header = data[pos:pos + 7]
        if len(header) < 7:
            break
        _crc, length, rtype = struct.unpack("<IHB", header)
        pos += 7
        if length == 0 and rtype == 0:
            pos += block_left - 7
            continue
        payload = data[pos:pos + length]
        pos += length
        if rtype == 1:  # full
            yield payload
            pending = b""
        elif rtype == 2:  # first
            pending = payload
        elif rtype == 3:  # middle
            pending += payload
        elif rtype == 4:  # last
            pending += payload
            yield pending
            pending = b""


def read_log(path):
    """Yield (seq, type, user_key, value) for every batch entry in a .log."""
    for payload in _log_records(path):
        if len(payload) < 12:
            continue
        seq = struct.unpack_from("<Q", payload, 0)[0]
        count = struct.unpack_from("<I", payload, 8)[0]
        pos = 12
        cur = seq
        for _ in range(count):
            if pos >= len(payload):
                break
            tag = payload[pos]
            pos += 1
            klen, pos = _varint(payload, pos)
            key = payload[pos:pos + klen]
            pos += klen
            if tag == 1:
                vlen, pos = _varint(payload, pos)
                value = payload[pos:pos + vlen]
                pos += vlen
                yield cur, 1, key, value
            elif tag == 0:
                yield cur, 0, key, b""
            else:
                break
            cur += 1


def db_files(dbdir):
    tables, logs = [], []
    for name in sorted(os.listdir(dbdir)):
        if name.endswith(".ldb") or name.endswith(".sst"):
            tables.append(os.path.join(dbdir, name))
        elif name.endswith(".log"):
            logs.append(os.path.join(dbdir, name))
    return tables, logs


def read_db(dbdir):
    """Return {user_key: value} for the live state of a LevelDB directory."""
    tables, logs = db_files(dbdir)
    latest = {}  # user_key -> (seq, type, value)
    for path in tables:
        for user_key, seq, typ, value in read_table(path):
            prev = latest.get(user_key)
            if prev is None or seq > prev[0]:
                latest[user_key] = (seq, typ, value)
    for path in logs:
        for seq, typ, user_key, value in read_log(path):
            prev = latest.get(user_key)
            if prev is None or seq >= prev[0]:
                latest[user_key] = (seq, typ, value)
    out = {}
    for key, (seq, typ, value) in latest.items():
        if typ == 1:
            out[key] = value
    return out


def stats(dbdir):
    tables, logs = db_files(dbdir)
    return {
        "tables": [(os.path.basename(p), os.path.getsize(p)) for p in tables],
        "logs": [(os.path.basename(p), os.path.getsize(p)) for p in logs],
    }
