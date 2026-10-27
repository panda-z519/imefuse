"""WeType (微信键盘) user-dictionary adapter.

Storage
-------
~/Library/Application Support/WeType/userDict/v5/<dbid>/   LevelDB

Key namespaces (keys are PLAINTEXT, values are shifted):
    !v2u!<pinyin>              user word + count + timestamp   <- the frequency table
    !u_d_v_p!<pinyin>\\x01<word>  user word index
    !dcact!<id>                per-entry engine records
    !user_bigram2!...          context bigrams
    !en_fe! / !env2!           english stats
    !prof! / !reii!            metadata

Value encoding: every byte is shifted by +6 (mod 256). After decoding, a
record ends with:

    <len-14:u8> <0xFA 0xFA 0xFA> <0x54 0x9F 0x54 0x9F>

which gives us a cheap structural check.

!v2u! value layout (decoded):
    [0]        0x00
    [1]        word length in bytes
    [2]        0x00
    [3:7]      count (u32 LE)
    [7:11]     last-used unix time (u32 LE)
    [11:15]    secondary counter (u32 LE)
    ...
    [n-8-wlen:n-8]  word (UTF-8)
    [n-8]      len-14
    [n-7:n-4]  0xFA 0xFA 0xFA
    [n-4:n]    0x54 0x9F 0x54 0x9F
"""
import os
import struct

from .. import paths, util
from ..leveldb import reader as ldb_reader
from ..leveldb import writer as ldb_writer

CAPS = {
    "has_count": True,
    "writable": True,
    "label": "微信键盘 WeType",
    "note": "!v2u! 命名空间带真实计数，可读可写可合并",
}

SHIFT = 6
TRAILER = b"\xfa\xfa\xfa\x54\x9f\x54\x9f"

NS_WORD = b"!v2u!"
NS_INDEX = b"!u_d_v_p!"
NS_ENTRY = b"!dcact!"
NS_BIGRAM = b"!user_bigram2!"


def data_root():
    return paths.expand("~/Library/Application Support/WeType")


def dict_dir():
    root = os.path.join(data_root(), "userDict", "v5")
    conf = os.path.join(root, "db_path.conf")
    name = None
    if os.path.exists(conf):
        name = util.read_text(conf).strip()
    if not name or not os.path.isdir(os.path.join(root, name)):
        cands = [d for d in os.listdir(root) if d.isdigit() and os.path.isdir(os.path.join(root, d))]
        if not cands:
            raise FileNotFoundError("WeType user dict dir not found under %s" % root)
        name = sorted(cands)[-1]
    return os.path.join(root, name)


def hotword_dir():
    return os.path.join(data_root(), "userDict", "user_hot_word")


def decode(buf):
    return bytes((b - SHIFT) & 0xFF for b in buf)


def encode(buf):
    return bytes((b + SHIFT) & 0xFF for b in buf)


def is_record(d):
    return (
        len(d) >= 14
        and d[-4:] == TRAILER[-4:]
        and d[-7:-4] == TRAILER[:3]
        and d[-8] == (len(d) - 14) & 0xFF
    )


def parse_v2u(key, raw):
    d = decode(raw)
    if len(d) < 16 or d[0] != 0 or not is_record(d):
        return None
    wlen = d[1]
    count = struct.unpack_from("<I", d, 3)[0]
    ts = struct.unpack_from("<I", d, 7)[0]
    extra = struct.unpack_from("<I", d, 11)[0]
    woff = len(d) - 8 - wlen
    if woff < 15:
        return None
    try:
        word = d[woff:woff + wlen].decode("utf-8")
        py = key[len(NS_WORD):].decode("ascii")
    except UnicodeDecodeError:
        return None
    return {"py": py, "w": word, "n": count, "t": ts, "extra": extra,
            "len": len(d), "wlen": wlen}


def parse_index(key):
    """!u_d_v_p!<pinyin>\\x01<word> -> (pinyin, word) straight from the key."""
    body = key[len(NS_INDEX):]
    if b"\x01" not in body:
        return None
    py, word = body.split(b"\x01", 1)
    try:
        return py.decode("ascii"), word.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_entry(key, raw):
    """!dcact!<id>: pinyin in the head blob, word inline after 0x01 when present."""
    d = decode(raw)
    if len(d) < 20 or d[0] != 0x03 or not is_record(d):
        return None
    plen = d[1]
    blob = d[3:3 + plen]
    if b"\x01" in blob:
        py, word = blob.split(b"\x01", 1)
    else:
        py = blob
        wlen = d[6 + plen] if len(d) > 6 + plen else 0
        woff = len(d) - 8 - wlen
        if wlen <= 0 or woff < 15:
            return None
        word = d[woff:woff + wlen]
    try:
        return py.decode("ascii"), word.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load():
    return ldb_reader.read_db(dict_dir())


def export():
    """Return canonical records for this IME (merge of the three namespaces)."""
    kv = load()
    out = {}
    for key, raw in kv.items():
        if key.startswith(NS_WORD):
            rec = parse_v2u(key, raw)
            if rec:
                out[(rec["py"], rec["w"])] = {
                    "w": rec["w"], "py": rec["py"].replace(",", " "),
                    "n": rec["n"], "t": rec["t"], "raw": rec["n"],
                    "kind": "user", "src": "wetype",
                }
    index_only = 0
    for key in kv:
        if key.startswith(NS_INDEX):
            p = parse_index(key)
            if p:
                py, word = p
                k = (py, word)
                if k not in out:
                    out[k] = {"w": word, "py": py.replace(",", " "), "n": 1,
                              "t": 0, "raw": 1, "kind": "user", "src": "wetype",
                              "n_est": True}
                    index_only += 1
    entries = 0
    for key, raw in kv.items():
        if key.startswith(NS_ENTRY):
            p = parse_entry(key, raw)
            if p:
                entries += 1
                py, word = p
                if (py, word) not in out:
                    out[(py, word)] = {"w": word, "py": py.replace(",", " "), "n": 1,
                                       "t": 0, "raw": 1, "kind": "dict", "src": "wetype",
                                       "n_est": True}
    return list(out.values()), {
        "kv_total": len(kv),
        "v2u": sum(1 for k in kv if k.startswith(NS_WORD)),
        "index": sum(1 for k in kv if k.startswith(NS_INDEX)),
        "entry": sum(1 for k in kv if k.startswith(NS_ENTRY)),
        "index_only": index_only,
    }


def template_for_word_len(kv, wlen):
    """Pick a well-formed !v2u! record whose word has the requested byte length."""
    best = None
    for key, raw in kv.items():
        if not key.startswith(NS_WORD):
            continue
        rec = parse_v2u(key, raw)
        if rec and rec["wlen"] == wlen:
            d = decode(raw)
            if best is None or rec["n"] > best[0]:
                best = (rec["n"], d, key)
    return None if best is None else best[1]


def build_v2u_value(template, word, count, ts):
    """Clone a template record, swapping word / count / timestamp / length byte."""
    d = bytearray(template)
    old_wlen = d[1]
    word_bytes = word.encode("utf-8")
    new_wlen = len(word_bytes)
    if new_wlen != old_wlen:
        raise ValueError("template word length mismatch")
    struct.pack_into("<I", d, 3, count)
    struct.pack_into("<I", d, 7, ts)
    struct.pack_into("<I", d, 11, 0)
    woff = len(d) - 8 - new_wlen
    d[woff:woff + new_wlen] = word_bytes
    d[-8] = (len(d) - 14) & 0xFF
    return encode(bytes(d))


def write_entries(entries, dry_run=True):
    """Merge (pinyin, word, count, ts) tuples into the !v2u! namespace.

    Existing keys are updated in place (count merged by the caller), new keys
    are cloned from a same-word-length template. Returns the WriteBatch plan.
    """
    dbdir = dict_dir()
    kv = load()
    seq = ldb_writer.max_seq(dbdir, ldb_reader) + 1
    plan = []
    for py, word, count, ts in entries:
        key = NS_WORD + py.replace(" ", ",").encode("ascii")
        tpl = template_for_word_len(kv, len(word.encode("utf-8")))
        if tpl is None:
            plan.append({"key": key, "status": "no-template"})
            continue
        value = build_v2u_value(tpl, word, count, ts)
        plan.append({"key": key, "status": "ok", "value": value})
    if dry_run:
        return plan, seq
    batch = [(p["key"], p["value"]) for p in plan if p["status"] == "ok"]
    payload = ldb_writer.encode_batch(seq, batch)
    ldb_writer.append_log(ldb_writer.current_log(dbdir), payload)
    return plan, seq


def check():
    """Validate that the on-disk store still matches the reversed format."""
    info = {"store": "LevelDB · 值 +6 位移 · !v2u! 词频表", "compatible": False, "detail": ""}
    try:
        dbdir = dict_dir()
    except FileNotFoundError as exc:
        info["detail"] = str(exc)
        return info
    for required in ("CURRENT",):
        if not os.path.exists(os.path.join(dbdir, required)):
            info["detail"] = "%s 不是 LevelDB 目录（缺 %s）" % (dbdir, required)
            return info
    kv = load()
    v2u = [k for k in kv if k.startswith(NS_WORD)]
    if not v2u:
        info["detail"] = "没有 !v2u! 词频记录，格式可能已变"
        return info
    sample = v2u[: min(len(v2u), 300)]
    good = 0
    for key in sample:
        d = decode(kv[key])
        if is_record(d) and d[0] == 0:
            good += 1
    ratio = good / len(sample)
    if ratio < 0.95:
        info["detail"] = "记录结构校验通过率仅 %.0f%%，疑似版本变更" % (ratio * 100)
        return info
    info["compatible"] = True
    info["detail"] = "!v2u! %d 条，结构校验 %.0f%%" % (len(v2u), ratio * 100)
    return info
