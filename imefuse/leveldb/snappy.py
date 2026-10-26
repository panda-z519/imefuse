"""Pure-python Snappy raw-block decompressor (the format LevelDB blocks use).

Snappy block format:
  varint(uncompressed_length)  then a stream of tags:
    tag & 3 == 0 : literal      len = tag>>2 ; if len < 60 -> len+1 bytes
                                  else len-59 extra bytes hold (len-1) little endian
    tag & 3 == 1 : copy, 1-byte offset   len = ((tag>>2)&7)+4 , off = ((tag>>5)<<8)|next
    tag & 3 == 2 : copy, 2-byte offset   len = (tag>>2)+1      , off = next 2 bytes LE
    tag & 3 == 3 : copy, 4-byte offset   len = (tag>>2)+1      , off = next 4 bytes LE
Copies may overlap the output being built, so they are applied byte by byte.
"""


def _varint(data, pos):
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def decompress(data):
    if not data:
        return b""
    length, pos = _varint(data, 0)
    out = bytearray()
    n = len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        kind = tag & 0x03
        if kind == 0:  # literal
            ln = tag >> 2
            if ln < 60:
                ln += 1
            else:
                extra = ln - 59
                ln = int.from_bytes(data[pos:pos + extra], "little") + 1
                pos += extra
            out += data[pos:pos + ln]
            pos += ln
            continue
        if kind == 1:
            ln = ((tag >> 2) & 0x07) + 4
            off = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        start = len(out) - off
        if start < 0:
            raise ValueError("snappy: offset before start of output")
        for i in range(ln):
            out.append(out[start + i])
    if len(out) != length:
        raise ValueError("snappy: length mismatch %d != %d" % (len(out), length))
    return bytes(out)
