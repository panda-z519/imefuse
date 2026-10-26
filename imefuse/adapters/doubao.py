"""Doubao IME (豆包输入法) user-dictionary adapter.

Storage
-------
~/Library/Application Support/DoubaoIme/EngineUserDictAccounts/<sha256(uid)>/
        usr.dat            <- the personal word/frequency table (this adapter)
        usr_side_dict.dat  <- side-code syllable table (not user words)
        usr_bigram.dat     <- user bigrams (context)
        usr_enhance.dat    <- user enhancement layer
        usr_history_bigram.dat
        usr_url.dat / usr_emailaddress.dat
        asr_user_ner_dict.dat
        engine_storage.leveldb  <- LevelDB "common phrase" record store (meta only
                                   until the engine commits; unrelated to usr.dat)

The account directory name is sha256 of the numeric uid taken from the MMKV
file ``.wave.account.sync/wave.account.userdict.owner``.

usr.dat container (all integers little-endian)
----------------------------------------------
    0x00 u32 dict_id           12001 for usr.dat
    0x04 u32 build_date        decimal YYYYMMDD, e.g. 20230630
    0x08 u32 header_size       64
    0x0c u32 file_size         total, preallocated
    0x10 u32 ext_offset        64
    0x14 u32 ext_size          140
    0x18 u32 index_offset      204  (= 64 + 140)
    0x1c u32 max_records       10000 (advisory)
    0x20 u32 index_entries     2500 (hard record capacity)
    0x24 u32 record_count
    0x28 u32 data_offset       10204 (= 204 + 2500*4)
    0x2c u32 data_capacity     80000
    0x30 u32 data_used
    0x34 u32 key_len_field     2
    0x38 u32 val_len_field     2
    0x3c u32 attr_size         16

    extended area (140 bytes at ext_offset) -- running statistics:
        +0x00 u32 max_pseudo_time
        +0x04 u32 last_update_time
        +0x08 u32 freq_sum          (one per Add)
        +0x0c u32 single_syllable_count
        +0x10 u32 pseudo_time_sum
        +0x14.. zeros

    index array at index_offset: 2500 * u32, index_array[i] = offset of record i
    inside the data region.

    record:
        +0x00 16 bytes attr: u32 freq, u32 pseudo_time, u32 ptime, u16 flags, u16 pad
        +0x10 u16 key_len (bytes)
        +0x12 key        -- one u16 "syllable code" per syllable
        ...   u16 value_len (bytes)
        ...   value      -- EncryptInputWord output (see below)

Value encoding (UsrDict::EncryptInputWord)
------------------------------------------
    <u16 pinyin_len><cipher_pinyin[pinyin_len]><u16 word_len><cipher_word[word_len]>
where the ciphers are single-byte / single-u16 substitutions:

    EncryptChar(c)    = rol8 (c ^ 0xA5, 5)          DecryptChar(e)    = rol8(e, 3) ^ 0xA5
    EncryptChar16(c)  = rol16(c ^ 0x65C9, 9)        DecryptChar16(e)  = rol16(e, 7) ^ 0x65C9

cipher_pinyin is the pinyin with separators removed ("hebingceshici"),
cipher_word is the UTF-16LE code units of the word ("合并测试次").

Syllable codes
--------------
code = 36 + index into the engine's sorted pinyin syllable table (413 entries,
including the bare "m" and "n" syllables).  Codes 0..35 are reserved.
The table was lifted verbatim out of OimeEngine (see docs/formats/doubao.md).
"""
import glob
import hashlib
import os
import struct

from .. import paths, util
import time

# ---------------------------------------------------------------- constants

HDR_SIZE = 64
EXT_OFF = 64
EXT_SIZE = 140
INDEX_OFF = 204
INDEX_ENTRIES = 2500
DATA_OFF = INDEX_OFF + INDEX_ENTRIES * 4          # 10204
ATTR_SIZE = 16

DICT_ID_USR = 12001

#: sorted pinyin syllable table used by oime_engine (OimeEngine binary @ 0x7a4b5f)
SYLLABLES = (
    "a ai an ang ao ba bai ban bang bao bei ben beng bi bian biao bie bin bing bo bu ca cai can cang cao "
    "ce cen ceng cha chai chan chang chao che chen cheng chi chong chou chu chua chuai chuan chuang chui "
    "chun chuo ci cong cou cu cuan cui cun cuo da dai dan dang dao de dei den deng di dia dian diao die d"
    "ing diu dong dou du duan dui dun duo e ei en eng er fa fan fang fei fen feng fiao fo fou fu ga gai g"
    "an gang gao ge gei gen geng gong gou gu gua guai guan guang gui gun guo ha hai han hang hao he hei h"
    "en heng hong hou hu hua huai huan huang hui hun huo ji jia jian jiang jiao jie jin jing jiong jiu ju"
    " juan jue jun ka kai kan kang kao ke kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo la lai"
    " lan lang lao le lei leng li lia lian liang liao lie lin ling liu lo long lou lu luan lue lun luo lv"
    " m ma mai man mang mao me mei men meng mi mian miao mie ming miu mo mou mu n na nai nang nao ne nei "
    "nen neng ni nian niang niao nie nin ning niu nong nou nu nuan nue nun nuo nv o ou pa pai pan pang pa"
    "o pei pen peng pi pian piao pie pin ping po pou pu qi qia qian qiang qiao qie qin qing qiong qiu qu "
    "quan que qun ran rang rao re ren reng ri rong rou ru rua ruan rui run ruo sa sai san sang sao se sen"
    " seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan shuang shui shun sh"
    "uo si song sou su suan sui sun suo ta tai tan tang tao te tei teng ti tian tiao tie ting tong tou tu"
    " tuan tui tun tuo wa wai wan wang wei wen weng wo wu xi xia xian xiang xiao xie xin xing xiong xiu x"
    "u xuan xue xun ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun za zai zan zang zao ze zei"
    " zen zeng zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan zhuang zh"
    "ui zhun zhuo zi zong zou zu zuan zui zun zuo"
).split()

SYL_CODE_BASE = 36
SYL_TO_CODE = {s: i + SYL_CODE_BASE for i, s in enumerate(SYLLABLES)}
CODE_TO_SYL = {c: s for s, c in SYL_TO_CODE.items()}


# ------------------------------------------------------------------- cipher

CAPS = {
    "has_count": True,
    "writable": True,
    "label": "豆包输入法",
    "note": "usr.dat 每条记录带 freq 字段，可读可写可合并",
}


def _rol8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _rol16(x, n):
    return ((x << n) | (x >> (16 - n))) & 0xFFFF


def decrypt_byte(e):
    return _rol8(e, 3) ^ 0xA5


def encrypt_byte(c):
    return _rol8(c ^ 0xA5, 5)


def decrypt_u16(e):
    return _rol16(e, 7) ^ 0x65C9


def encrypt_u16(c):
    return _rol16(c ^ 0x65C9, 9)


def encrypt_str(s):
    return bytes(encrypt_byte(b) for b in s.encode("ascii"))


def decrypt_str(buf):
    return bytes(decrypt_byte(b) for b in buf).decode("ascii", "replace")


def encrypt_u16str(s):
    return [encrypt_u16(ord(ch)) for ch in s]


def decrypt_u16str(codes):
    return "".join(chr(decrypt_u16(c)) for c in codes)


# -------------------------------------------------------------- data roots

def data_root():
    return paths.expand("~/Library/Application Support/DoubaoIme")


def account_uid():
    """Numeric uid of the logged-in Doubao account, from the MMKV owner file."""
    p = os.path.join(data_root(), ".wave.account.sync", "wave.account.userdict.owner")
    try:
        blob = util.read_bytes(p)
    except OSError:
        return None
    marker = b'"loggedIn":{"uid":"'
    i = blob.find(marker)
    if i < 0:
        return None
    j = blob.find(b'"', i + len(marker))
    if j < 0:
        return None
    try:
        return blob[i + len(marker):j].decode("ascii")
    except UnicodeDecodeError:
        return None


def dict_dir():
    """Directory holding usr.dat for the active account."""
    base = os.path.join(data_root(), "EngineUserDictAccounts")
    uid = account_uid()
    if uid:
        d = os.path.join(base, hashlib.sha256(uid.encode()).hexdigest())
        if os.path.isfile(os.path.join(d, "usr.dat")):
            return d
    cands = []
    for d in glob.glob(os.path.join(base, "*")):
        p = os.path.join(d, "usr.dat")
        if os.path.isfile(p) and os.path.getsize(p) >= DATA_OFF:
            cands.append((os.path.getmtime(p), d))
    if not cands:
        raise FileNotFoundError("Doubao user dict not found under %s" % base)
    return sorted(cands)[-1][1]


def usr_dat_path():
    return os.path.join(dict_dir(), "usr.dat")


# ------------------------------------------------------------ container io

class Header(object):
    __slots__ = ("dict_id", "build_date", "header_size", "file_size", "ext_offset",
                 "ext_size", "index_offset", "max_records", "index_entries",
                 "record_count", "data_offset", "data_capacity", "data_used",
                 "key_len_field", "val_len_field", "attr_size")

    _FIELDS = __slots__

    def __init__(self, *vals):
        for k, v in zip(self._FIELDS, vals):
            setattr(self, k, v)

    @classmethod
    def unpack(cls, buf):
        vals = struct.unpack_from("<16I", buf, 0)
        return cls(*vals)

    def pack(self):
        return struct.pack("<16I", *[getattr(self, k) for k in self._FIELDS])


class Record(object):
    """One user-dict entry, exactly as stored."""
    __slots__ = ("attr", "key", "value", "offset", "size")

    def __init__(self, attr, key, value, offset=0):
        self.attr = attr          # 16 raw bytes
        self.key = key            # bytes (u16 syllable codes)
        self.value = value        # bytes (encrypted pinyin+word)
        self.offset = offset
        self.size = ATTR_SIZE + 2 + len(key) + 2 + len(value)

    # -- attr accessors
    @property
    def freq(self):
        return struct.unpack_from("<I", self.attr, 0)[0]

    @property
    def pseudo_time(self):
        return struct.unpack_from("<I", self.attr, 4)[0]

    @property
    def ptime(self):
        return struct.unpack_from("<I", self.attr, 8)[0]

    @property
    def flags(self):
        return struct.unpack_from("<H", self.attr, 12)[0]

    # -- key
    def syllables(self):
        codes = struct.unpack("<%dH" % (len(self.key) // 2), self.key)
        return [CODE_TO_SYL.get(c) for c in codes], codes

    # -- value
    def decode(self):
        v = self.value
        if len(v) < 4:
            return None
        plen = struct.unpack_from("<H", v, 0)[0]
        if 2 + plen + 2 > len(v):
            return None
        py = decrypt_str(v[2:2 + plen])
        wlen = struct.unpack_from("<H", v, 2 + plen)[0]
        woff = 4 + plen
        if woff + wlen * 2 > len(v):
            return None
        codes = struct.unpack_from("<%dH" % wlen, v, woff)
        word = decrypt_u16str(codes)
        return py, word

    def serialize(self):
        return (self.attr
                + struct.pack("<H", len(self.key)) + self.key
                + struct.pack("<H", len(self.value)) + self.value)


def parse(buf):
    """Parse a usr.dat image -> (header, ext_bytes, records, index_array)."""
    h = Header.unpack(buf)
    if h.header_size != HDR_SIZE or h.data_offset + h.data_capacity > len(buf):
        raise ValueError("not a Doubao usr.dat container")
    ext = buf[h.ext_offset:h.ext_offset + h.ext_size]
    idx = list(struct.unpack_from("<%dI" % h.index_entries, buf, h.index_offset))
    recs = []
    off = 0
    for i in range(h.record_count):
        # the index array is authoritative; fall back to a sequential walk
        roff = idx[i] if i < len(idx) and (i == 0 or idx[i] != 0) else off
        p = h.data_offset + roff
        if p + ATTR_SIZE + 4 > len(buf):
            break
        attr = buf[p:p + ATTR_SIZE]
        klen = struct.unpack_from("<H", buf, p + ATTR_SIZE)[0]
        koff = p + ATTR_SIZE + 2
        key = buf[koff:koff + klen]
        vlen = struct.unpack_from("<H", buf, koff + klen)[0]
        voff = koff + klen + 2
        value = buf[voff:voff + vlen]
        rec = Record(attr, key, value, roff)
        recs.append(rec)
        off = roff + rec.size
    return h, ext, recs, idx


def _key_tuple(rec):
    codes = struct.unpack("<%dH" % (len(rec.key) // 2), rec.key)
    return codes


def key_compare(a, b):
    """UsrDict::KeyCompare -- numeric u16 lexicographic, shorter prefix first."""
    ka, kb = _key_tuple(a), _key_tuple(b)
    n = min(len(ka), len(kb))
    for i in range(n):
        if ka[i] != kb[i]:
            return -1 if ka[i] < kb[i] else 1
    if len(ka) != len(kb):
        return -1 if len(ka) < len(kb) else 1
    return 0


def key_value_compare(a, b):
    """(key, value) ordering -- key first, raw value bytes as tiebreak."""
    c = key_compare(a, b)
    if c:
        return c
    if a.value == b.value:
        return 0
    return -1 if a.value < b.value else 1


def build_image(header, records, ext_update=None, now=None):
    """Serialise a full usr.dat image (same size as the original)."""
    h = Header(*[getattr(header, k) for k in Header._FIELDS])
    h.record_count = len(records)
    if h.record_count > h.index_entries:
        raise ValueError("record count %d exceeds capacity %d"
                         % (h.record_count, h.index_entries))

    data = bytearray()
    index = [0] * h.index_entries
    for i, rec in enumerate(records):
        rec.offset = len(data)
        index[i] = len(data)
        data += rec.serialize()
    if len(data) > h.data_capacity:
        raise ValueError("data %d exceeds capacity %d" % (len(data), h.data_capacity))
    h.data_used = len(data)

    ext = bytearray(h.ext_size)
    if records:
        struct.pack_into("<I", ext, 0x00, max(r.pseudo_time for r in records))
        struct.pack_into("<I", ext, 0x08, sum(r.freq for r in records))
        struct.pack_into("<I", ext, 0x0c,
                         sum(1 for r in records if len(r.key) == 2))
        struct.pack_into("<I", ext, 0x10, sum(r.pseudo_time for r in records))
    if ext_update:
        for off, val in ext_update.items():
            struct.pack_into("<I", ext, off, val)
    if now is not None:
        struct.pack_into("<I", ext, 0x04, now)

    out = bytearray(h.file_size)
    out[0:HDR_SIZE] = h.pack()
    out[h.ext_offset:h.ext_offset + h.ext_size] = ext
    struct.pack_into("<%dI" % h.index_entries, out, h.index_offset, *index)
    out[h.data_offset:h.data_offset + len(data)] = data
    return bytes(out), h


# ------------------------------------------------------------------ public

def export(path=None):
    """Return canonical records for the Doubao IME user dictionary."""
    path = path or usr_dat_path()
    buf = util.read_bytes(path)
    h, ext, recs, idx = parse(buf)
    out = []
    bad = 0
    for rec in recs:
        dec = rec.decode()
        if not dec:
            bad += 1
            continue
        py_raw, word = dec
        syls, codes = rec.syllables()
        if all(syls):
            py = " ".join(syls)
        else:                       # unknown code -> fall back to the plain pinyin
            py = py_raw
        out.append({
            "w": word,
            "py": py,
            "n": rec.freq,
            "t": rec.ptime,
            "raw": rec.value.hex(),
            "kind": "user",
            "src": "doubao",
            "_pseudo": rec.pseudo_time,
            "_flags": rec.flags,
            "_codes": list(codes),
            "_py_raw": py_raw,
        })
    stats = {
        "path": path,
        "dict_id": h.dict_id,
        "build_date": h.build_date,
        "file_size": h.file_size,
        "record_count": h.record_count,
        "data_used": h.data_used,
        "data_capacity": h.data_capacity,
        "index_entries": h.index_entries,
        "freq_sum": struct.unpack_from("<I", ext, 0x08)[0],
        "pseudo_time_sum": struct.unpack_from("<I", ext, 0x10)[0],
        "last_update": struct.unpack_from("<I", ext, 0x04)[0],
        "single_syllable": struct.unpack_from("<I", ext, 0x0c)[0],
        "undecodable": bad,
    }
    return out, stats


def _make_record(py, word, freq, ptime, pseudo, flags=0):
    syls = py.replace(",", " ").split()
    codes = []
    for s in syls:
        c = SYL_TO_CODE.get(s)
        if c is None:
            raise ValueError("unknown pinyin syllable: %r" % s)
        codes.append(c)
    key = struct.pack("<%dH" % len(codes), *codes)
    py_raw = "".join(syls)
    cp = encrypt_str(py_raw)
    cw = encrypt_u16str(word)
    value = (struct.pack("<H", len(cp)) + cp
             + struct.pack("<H", len(cw)) + struct.pack("<%dH" % len(cw), *cw))
    attr = struct.pack("<II", freq, pseudo) + struct.pack("<I", ptime) \
        + struct.pack("<H", flags) + b"\x00\x00"
    return Record(attr, key, value)


def write_entries(entries, dry_run=True, path=None, backup=True):
    """Merge entries into usr.dat.  Add-only: existing words are never touched.

    ``entries`` is an iterable of (pinyin, word, count, ts) or dicts with
    ``py`` / ``w`` / ``n`` / ``t``.  Returns a plan describing every entry.
    """
    path = path or usr_dat_path()
    buf = util.read_bytes(path)
    h, ext, recs, idx = parse(buf)

    existing = {}
    for rec in recs:
        dec = rec.decode()
        if dec:
            existing.setdefault(dec[1], rec)

    max_pseudo = max([r.pseudo_time for r in recs] or [0])
    plan = []
    added = []
    for e in entries:
        if isinstance(e, dict):
            py, word = e.get("py"), e.get("w")
            freq = int(e.get("n") or 1)
            ts = int(e.get("t") or 0)
        else:
            py, word, freq, ts = e
            freq, ts = int(freq), int(ts)
        if word in existing:
            plan.append({"w": word, "py": py, "status": "exists",
                         "reason": "word already in dict (add-only, not touched)"})
            continue
        try:
            max_pseudo += 1
            rec = _make_record(py, word, freq, ts or 0, max_pseudo)
        except ValueError as exc:
            plan.append({"w": word, "py": py, "status": "bad-pinyin",
                         "reason": str(exc)})
            continue
        added.append(rec)
        plan.append({"w": word, "py": py, "status": "add",
                     "key": rec.key.hex(), "value": rec.value.hex()})

    if not added:
        return plan, {"added": 0, "existing": len(recs)}

    merged = list(recs) + added
    merged.sort(key=_SortKey)          # stable: existing relative order preserved
    newbuf, nh = build_image(h, merged, now=int(time.time()))

    info = {"added": len(added), "total": nh.record_count,
            "data_used": nh.data_used, "size": len(newbuf)}
    if dry_run:
        info["dry_run"] = True
        return plan, info

    if backup:
        bak = path + ".imefuse-bak"
        if not os.path.exists(bak):
            with open(bak, "wb") as fh:
                fh.write(buf)
    tmp = path + ".imefuse-tmp"
    with open(tmp, "wb") as fh:
        fh.write(newbuf)
    os.replace(tmp, path)
    return plan, info


class _SortKey(object):
    """functools.cmp_to_key replacement for key_value_compare."""
    __slots__ = ("rec",)

    def __init__(self, rec):
        self.rec = rec

    def __lt__(self, other):
        return key_value_compare(self.rec, other.rec) < 0


def check():
    """Validate the usr.dat container geometry."""
    info = {"store": "usr.dat · 音节码 + UTF-16 加密 · 定长索引", "compatible": False, "detail": ""}
    try:
        path = usr_dat_path()
        buf = util.read_bytes(path)
    except (OSError, FileNotFoundError) as exc:
        info["detail"] = str(exc)
        return info
    try:
        h, ext, recs, idx = parse(buf)
    except Exception as exc:  # noqa: BLE001
        info["detail"] = "解析失败：%s" % exc
        return info
    if h.dict_id != 12001:
        info["detail"] = "dict_id %d 未适配（已知 12001）" % h.dict_id
        return info
    expect = h.header_size + h.ext_size + h.index_entries * 4 + h.data_capacity
    if expect != len(buf):
        info["detail"] = "容器几何对不上：推算 %d 实际 %d" % (expect, len(buf))
        return info
    if h.record_count != len(recs):
        info["detail"] = "记录数不一致：头 %d 实际 %d" % (h.record_count, len(recs))
        return info
    info["compatible"] = True
    info["detail"] = "dict_id 12001 · %d/%d 条 · data %d/%d" % (
        h.record_count, h.index_entries, h.data_used, h.data_capacity)
    return info
