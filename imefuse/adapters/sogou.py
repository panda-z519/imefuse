"""Sogou IME (搜狗输入法 for macOS) user-dictionary adapter.

Storage
-------
~/Library/Application Support/Sogou/InputMethod/
    SogouPY/                        global / default data (empty skeleton)
    SogouPY.users/<userid>/         the real per-user directory
        sgim_gd_usr.bin             <-- THIS adapter: user lexicon (magic "SGPU")
        sgim_gd_usr.bin.bak         pristine skeleton (0 records, 440140 bytes)
        sgim_gd_usr_od.bin          (SGOU) secondary lexicon, insertion-ordered
        sgim_gd_usrbg.bin           (SGBG) user bigrams
        sgim_gd_cell.bin            (CELL) cell(pinyin) -> candidate table
        sgim_gd_ext.bin             (_EXT) extension dictionary
        sgim_gd_usr_a_bigram.bin    (SGAB) adaptive bigram, per-session

Container "SGPU"
---------------
    +0x00  char[4]  "SGPU"
    +0x04  u32      header size = 0x28
    +0x08  u32      data-format version packed as YYYYMMDD (0x0133a009 -> 20160521)
    +0x0c  u32      0
    +0x10  u32      total file size
    +0x14  u32      1
    +0x18  u32      0x10
    +0x1c  u32      0
    +0x20  u32      save nonce (changes on every save; not crc32/adler32)
    +0x24  u32      0x54  (end of the extended directory)
    --- directory -------------------------------------------------------
    +0x28  u32      generation counter: max `gen` in the arena (empty file -> 1)
    +0x2c  u32      0
    +0x30  u32      0
    +0x34  u32      ~ sum of every record's `gen` (off by a few thousand in practice)
    +0x38  u32      index offset   (0x8c, right after the header block)
    +0x3c  u32      index size     (bytes; slots = size / 4)
    +0x40  u32      record count
    +0x44  u32      arena offset
    +0x48  u32      arena size     (capacity, bytes)
    +0x4c  u32      arena used     (bytes actually holding records)
    +0x50  u32      9

Index section
-------------
    record count x u32, ascending by the record's pinyin-id sequence.  Each value
    is an ARENA-RELATIVE offset of a record start; 0 is a legal value (it is the
    first record).  The entries occupy slots [0, count) and the tail is unused
    slack that lets the engine insert without reallocating - so a 0 in the tail is
    "free", not a hole marker; the live part of the array is dense.

Arena record
------------
    +0   u16   tag       1 for an ordinary entry; 2..N on entries the engine
                         re-wrote in the current save (= 1 + update events)
    +2   u32   gen       save generation the entry was last written in
    +6   u16   1         flags
    +8   u8    type      0x03 = normal entry, 0x02 = re-written in the last save
    +9   u16   hl        head length in bytes
    +11  u16[] head      pinyin ids (hl/2 of them) - the canonical key
    +11+hl     u16 bl    body length in bytes
    +13+hl     u16 wlen  word length in bytes (UTF-16LE)
    +15+hl     wchar[] word
    +15+hl+wlen u16 pylen  == hl (the id list is stored twice)
    +17+hl+wlen u16[] trail second copy of the id list
    total = 15 + hl + bl        (bl = 4 + wlen + pylen)

`len(ids) == number of UTF-16 code units of the word` for every record, i.e. one
pinyin id per character.  `head` and `trail` are identical in 33459 of 33460
records; the one exception is a record the engine itself wrote (搜狗, trail id
900 instead of 312), so `head` is treated as canonical and `trail` is what the
engine sorts the index by.

Pinyin ids index the syllable table
`SogouInput.app/Contents/Resources/SogouImeKernelResources.bundle/SogouInput/
sgim_InputStr.bin` - a newline separated list.  0..412 are the pinyin syllables
in alphabetical order, 413..438 are 'A'..'Z', 439..447 are digits; the file
keeps a second, unused list past 447 that this dictionary never references.
Verified against 23 known words (ce=26, ni=219, hao=117, shi=300, sou=312, ...)
with zero mismatches.

There is NO plain hit counter
-----------------------------
`gen` is a *save generation*, not a frequency.  Proofs:

1. Block sizes.  The arena is split into contiguous blocks by `gen`, ordered by
   `gen`, and the sizes start [8207, 25, 60, 54, 3, 34, 8, 43, 58, 37, ...].  A
   hit-count distribution cannot look like that (8207 words with count 1 but
   only 25 with count 2) and it is not monotone either.
2. Live capture.  One hour apart the counter went 787 -> 797 and the newest
   generations held exactly the words that had just been typed:
       gen 791 输入法   gen 794 测试   gen 795 搜狗   gen 796 你好   gen 797 世界
   Those five are also the only records with type=2.  你好 was typed three times
   and 世界 twice, yet each keeps a single record at its original arena offset -
   the engine rewrites the entry in place instead of accumulating a count.
   (One of the five, 搜狗, was instead appended a second time by the engine with
   a corrupted trail id and a stale tag; export collapses such duplicates and
   keeps the newest generation.)

The only usage-derived number in the file is `tag`: 1 for all 33455 untouched
entries, and 1 + update-count on the five re-written ones (输入法 2, 搜狗 2,
测试 3, 世界 3, 你好 4 - which matches "1 + times typed" for 4 of the 5; 搜狗 is
also the record with the corrupted trail id, so that sample looks damaged).
`tag` is exported through `stats`, not through `n`, because it saturates at 1 for
the whole inherited lexicon.

Write-back is therefore append-only: new words can be added, counts cannot be
expressed.
"""
import os
import re
import shutil
import struct

from .. import paths, util

MAGIC = b"SGPU"
HEADER_SIZE = 0x28
DIR_END = 0x54

REC_TYPE_NORMAL = 0x03
REC_TYPE_REWRITTEN = 0x02
NEW_REC_TAG = 1
NEW_REC_TYPE = REC_TYPE_NORMAL

# ids actually used by the dictionary
LETTER_BASE = 413
MAX_ID = 447

# directory field offsets
O_VERSION = 0x08
O_SIZE = 0x10
O_NONCE = 0x20
O_GEN = 0x28
O_GENSUM = 0x34
O_IDX_OFF = 0x38
O_IDX_SIZE = 0x3C
O_COUNT = 0x40
O_ARENA_OFF = 0x44
O_ARENA_SIZE = 0x48
O_ARENA_USED = 0x4C
O_TAIL = 0x50

# ---------------------------------------------------------------- paths


CAPS = {
    "has_count": False,
    "writable": True,
    "label": "搜狗输入法",
    "note": ("可写：写回需同步更新头部 O_NONCE 内容校验（base+arena_used+count），"
             "否则引擎会判定文件损坏并删除。词库只存词条与保存代次，没有词频计数器"),
}


def data_root():
    return paths.expand("~/Library/Application Support/Sogou/InputMethod")


def user_dirs():
    root = os.path.join(data_root(), "SogouPY.users")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and name.isdigit():
            out.append(d)
    return out


def _count_of(path):
    try:
        return read_container(path).count
    except (OSError, ValueError, struct.error):
        return -1


def dict_dir():
    """The user directory that actually holds records (highest record count)."""
    best = None
    for d in user_dirs():
        p = os.path.join(d, "sgim_gd_usr.bin")
        if not os.path.exists(p):
            continue
        n = _count_of(p)
        if best is None or n > best[0]:
            best = (n, d)
    if best is not None:
        return best[1]
    dirs = user_dirs()
    if dirs:
        return dirs[0]
    return os.path.join(data_root(), "SogouPY")


def usr_bin_path():
    return os.path.join(dict_dir(), "sgim_gd_usr.bin")


# ------------------------------------------------- syllable id table

_SYLLABLE_FILE = ("Contents/Resources/SogouImeKernelResources.bundle/"
                  "SogouInput/sgim_InputStr.bin")

_SYLLABLE_CANDIDATES = [
    os.path.join("/Library/Input Methods/SogouInput.app", _SYLLABLE_FILE),
    os.path.join(os.path.expanduser("~/Library/Input Methods/SogouInput.app"),
                 _SYLLABLE_FILE),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "work", "sogou", "SogouInput.app",
        _SYLLABLE_FILE),
]

_EMBEDDED_SYLLABLES = [
    'a', 'ai', 'an', 'ang', 'ao', 'ba', 'bai', 'ban', 'bang', 'bao', 'bei', 'ben',
    'beng', 'bi', 'bian', 'biao', 'bie', 'bin', 'bing', 'bo', 'bu', 'ca', 'cai', 'can',
    'cang', 'cao', 'ce', 'cen', 'ceng', 'cha', 'chai', 'chan', 'chang', 'chao', 'che', 'chen',
    'cheng', 'chi', 'chong', 'chou', 'chu', 'chua', 'chuai', 'chuan', 'chuang', 'chui', 'chun', 'chuo',
    'ci', 'cong', 'cou', 'cu', 'cuan', 'cui', 'cun', 'cuo', 'da', 'dai', 'dan', 'dang',
    'dao', 'de', 'dei', 'den', 'deng', 'di', 'dia', 'dian', 'diao', 'die', 'ding', 'diu',
    'dong', 'dou', 'du', 'duan', 'dui', 'dun', 'duo', 'e', 'ei', 'en', 'eng', 'er',
    'fa', 'fan', 'fang', 'fei', 'fen', 'feng', 'fiao', 'fo', 'fou', 'fu', 'ga', 'gai',
    'gan', 'gang', 'gao', 'ge', 'gei', 'gen', 'geng', 'gong', 'gou', 'gu', 'gua', 'guai',
    'guan', 'guang', 'gui', 'gun', 'guo', 'ha', 'hai', 'han', 'hang', 'hao', 'he', 'hei',
    'hen', 'heng', 'hong', 'hou', 'hu', 'hua', 'huai', 'huan', 'huang', 'hui', 'hun', 'huo',
    'ji', 'jia', 'jian', 'jiang', 'jiao', 'jie', 'jin', 'jing', 'jiong', 'jiu', 'ju', 'juan',
    'jue', 'jun', 'ka', 'kai', 'kan', 'kang', 'kao', 'ke', 'kei', 'ken', 'keng', 'kong',
    'kou', 'ku', 'kua', 'kuai', 'kuan', 'kuang', 'kui', 'kun', 'kuo', 'la', 'lai', 'lan',
    'lang', 'lao', 'le', 'lei', 'leng', 'li', 'lia', 'lian', 'liang', 'liao', 'lie', 'lin',
    'ling', 'liu', 'lo', 'long', 'lou', 'lu', 'luan', 'lue', 'lun', 'luo', 'lv', 'ma',
    'mai', 'man', 'mang', 'mao', 'me', 'mei', 'men', 'meng', 'mi', 'mian', 'miao', 'mie',
    'min', 'ming', 'miu', 'mo', 'mou', 'mu', 'na', 'nai', 'nan', 'nang', 'nao', 'ne',
    'nei', 'nen', 'neng', 'ni', 'nian', 'niang', 'niao', 'nie', 'nin', 'ning', 'niu', 'nong',
    'nou', 'nu', 'nuan', 'nue', 'nun', 'nuo', 'nv', 'o', 'ou', 'pa', 'pai', 'pan',
    'pang', 'pao', 'pei', 'pen', 'peng', 'pi', 'pian', 'piao', 'pie', 'pin', 'ping', 'po',
    'pou', 'pu', 'qi', 'qia', 'qian', 'qiang', 'qiao', 'qie', 'qin', 'qing', 'qiong', 'qiu',
    'qu', 'quan', 'que', 'qun', 'ran', 'rang', 'rao', 're', 'ren', 'reng', 'ri', 'rong',
    'rou', 'ru', 'rua', 'ruan', 'rui', 'run', 'ruo', 'sa', 'sai', 'san', 'sang', 'sao',
    'se', 'sen', 'seng', 'sha', 'shai', 'shan', 'shang', 'shao', 'she', 'shei', 'shen', 'sheng',
    'shi', 'shou', 'shu', 'shua', 'shuai', 'shuan', 'shuang', 'shui', 'shun', 'shuo', 'si', 'song',
    'sou', 'su', 'suan', 'sui', 'sun', 'suo', 'ta', 'tai', 'tan', 'tang', 'tao', 'te',
    'tei', 'teng', 'ti', 'tian', 'tiao', 'tie', 'ting', 'tong', 'tou', 'tu', 'tuan', 'tui',
    'tun', 'tuo', 'wa', 'wai', 'wan', 'wang', 'wei', 'wen', 'weng', 'wo', 'wu', 'xi',
    'xia', 'xian', 'xiang', 'xiao', 'xie', 'xin', 'xing', 'xiong', 'xiu', 'xu', 'xuan', 'xue',
    'xun', 'ya', 'yan', 'yang', 'yao', 'ye', 'yi', 'yin', 'ying', 'yo', 'yong', 'you',
    'yu', 'yuan', 'yue', 'yun', 'za', 'zai', 'zan', 'zang', 'zao', 'ze', 'zei', 'zen',
    'zeng', 'zha', 'zhai', 'zhan', 'zhang', 'zhao', 'zhe', 'zhei', 'zhen', 'zheng', 'zhi', 'zhong',
    'zhou', 'zhu', 'zhua', 'zhuai', 'zhuan', 'zhuang', 'zhui', 'zhun', 'zhuo', 'zi', 'zong', 'zou',
    'zu', 'zuan', 'zui', 'zun', 'zuo', 'A', 'B', 'C', 'D', 'E', 'F', 'G',
    'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S',
    'T', 'U', 'V', 'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4',
    '5', '6', '7', '8', '9', '±êµã', '!', '"', '#', '$', '%', '&',
    "'", '(', ')', '*', '+', ',', '-', '.', '/', ':', ';', '<',
    '=', '>', '?', '@', '[', '\\', ']', '^', '_', '`', '{', '|',
    '}', '~', 'seg', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*', '*',
    '22', '224', '226', '2264', '23', '234', '236', '2364', '24', '242', '2424', '2426',
    '24264', '243', '2436', '24364', '244', '246', '2464', '2466', '24664', '2468', '248', '2482',
    '24824', '24826', '248264', '2484', '2486', '26', '264', '266', '2664', '268', '28', '282',
    '2826', '284', '286', '32', '324', '326', '3264', '33', '334', '336', '3364', '34',
    '342', '3426', '343', '346', '3464', '348', '36', '364', '366', '3664', '368', '37',
    '38', '382', '3826', '384', '386', '42', '424', '426', '4264', '43', '434', '436',
    '4364', '46', '466', '4664', '468', '48', '482', '4824', '4826', '48264', '484', '486',
    '52', '524', '526', '5264', '53', '534', '536', '5364', '54', '542', '5426', '54264',
    '543', '546', '5464', '5466', '54664', '548', '56', '566', '5664', '568', '58', '582',
    '5824', '5826', '58264', '583', '584', '586', '62', '624', '626', '6264', '63', '634',
    '636', '6364', '64', '642', '6426', '64264', '643', '646', '6464', '648', '66', '666',
    '6664', '668', '68', '682', '6826', '683', '686', '72', '724', '726', '7264', '73',
    '734', '736', '7364', '74', '742', '7424', '7426', '74264', '743', '7434', '7436', '74364',
    '744', '746', '7464', '7466', '74664', '7468', '748', '7482', '74824', '74826', '748264', '7484',
    '7486', '76', '766', '7664', '768', '78', '782', '7826', '783', '784', '786', '82',
    '824', '826', '8264', '83', '834', '836', '8364', '84', '842', '8426', '843', '846',
    '8464', '86', '866', '8664', '868', '88', '882', '8826', '884', '886', '92', '924',
    '926', '9264', '93', '934', '936', '9364', '94', '942', '9424', '9426', '94264', '943',
    '9434', '9436', '94364', '944', '946', '9464', '9466', '94664', '9468', '948', '9482', '94824',
    '94826', '948264', '9484', '9486', '96', '966', '9664', '968', '98', '982', '9826', '983',
    '984', '986', 'b', 'be', 'bia', 'c', 'ch', 'cho', 'chon', 'co', 'con', 'cua',
    'd', 'din', 'do', 'don', 'dua', 'f', 'fe', 'fi', 'fia', 'g', 'go', 'gon',
    'h', 'ho', 'hon', 'i', 'j', 'jio', 'jion', 'jua', 'k', 'ko', 'kon', 'l',
    'len', 'lon', 'lua', 'm', 'mia', 'n', 'nia', 'no', 'non', 'nua', 'p', 'pe',
    'pia', 'q', 'qio', 'qion', 'qua', 'r', 'ra', 'ro', 'ron', 's', 'sh', 'sho',
    'so', 'son', 'sua', 't', 'ten', 'tia', 'tin', 'to', 'ton', 'tua', 'u', 'v',
    'w', 'we', 'x', 'xio', 'xion', 'xua', 'y', 'yon', 'yua', 'z', 'zh', 'zho',
    'zhon', 'zo', 'zon', 'zua',
]

_syllable_cache = [None, None, None]


def syllable_table():
    """id -> pinyin syllable ('' when out of range)."""
    if _syllable_cache[0] is None:
        table = None
        for p in _SYLLABLE_CANDIDATES:
            try:
                raw = util.read_bytes(p)
            except OSError:
                continue
            items = raw.decode("latin-1").split("\n")
            while items and items[-1] == "":
                items.pop()
            if len(items) > 400 and items[0] == "a":
                table = items
                break
        if table is None:
            table = list(_EMBEDDED_SYLLABLES)
        _syllable_cache[0] = table
        syl, letter = {}, {}
        for i, name in enumerate(table):
            if i > MAX_ID:
                break
            low = name.lower()
            if i < LETTER_BASE:
                syl.setdefault(low, i)          # 0..412 pinyin syllables
            elif len(low) == 1:
                letter.setdefault(low, i)       # 413..447 letters / digits
        _syllable_cache[1] = syl
        _syllable_cache[2] = letter
    return _syllable_cache[0]


def syllable_index():
    syllable_table()
    return _syllable_cache[1]


def _normalize_syllable(s):
    s = s.strip().lower()
    for a, b in (("\u00fc", "v"), ("u:", "v"), ("\u00ea", "e")):
        s = s.replace(a, b)
    return s


def ids_for(pinyin, word=None):
    """'ni hao' -> (219, 117); None if any syllable is unknown.

    The engine picks the id space per character: a latin/digit character of the
    word is stored as a letter id (413..447), a CJK character as a syllable id.
    Passing `word` reproduces that exactly (e.g. "Vu额" -> V u e).
    """
    syllable_table()
    syl, letter = _syllable_cache[1], _syllable_cache[2]
    parts = [p for p in _normalize_syllable(pinyin).replace(",", " ").split(" ") if p]
    if not parts:
        return None
    if word is not None and len(word) == len(parts):
        out = []
        for ch, tok in zip(word, parts):
            if ord(ch) < 128:
                if tok in letter:
                    out.append(letter[tok])
                    continue
                return None
            if tok in syl:
                out.append(syl[tok])
                continue
            return None
        return tuple(out)
    out = []
    for tok in parts:
        if tok in syl:
            out.append(syl[tok])
            continue
        if len(tok) > 1 and re.match(r"^[a-z0-9]+$", tok):
            for ch in tok:                      # raw latin run: one id per char
                if ch not in letter:
                    return None
                out.append(letter[ch])
            continue
        return None
    return tuple(out)


def pinyin_for(ids):
    table = syllable_table()
    return " ".join(table[i].lower() if i < len(table) else "" for i in ids)


# ------------------------------------------------------------- container


class Container(object):
    __slots__ = ("buf", "version", "size", "nonce", "gen", "gen_sum",
                 "idx_off", "idx_size", "count", "arena_off", "arena_size",
                 "arena_used", "tail")

    def __init__(self, buf):
        if buf[:4] != MAGIC:
            raise ValueError("not a SGPU container: magic=%r" % (buf[:4],))
        self.buf = buf
        u = lambda o: struct.unpack_from("<I", buf, o)[0]
        self.version = u(O_VERSION)
        self.size = u(O_SIZE)
        self.nonce = u(O_NONCE)
        self.gen = u(O_GEN)
        self.gen_sum = u(O_GENSUM)
        self.idx_off = u(O_IDX_OFF)
        self.idx_size = u(O_IDX_SIZE)
        self.count = u(O_COUNT)
        self.arena_off = u(O_ARENA_OFF)
        self.arena_size = u(O_ARENA_SIZE)
        self.arena_used = u(O_ARENA_USED)
        self.tail = u(O_TAIL)

    @property
    def slots(self):
        return self.idx_size // 4

    def index(self):
        return list(struct.unpack_from("<%dI" % self.slots, self.buf, self.idx_off))

    def record_at(self, abs_off):
        """(gen, tag, type, word_bytes, head_ids, trail_ids, reclen)."""
        buf = self.buf
        tag = struct.unpack_from("<H", buf, abs_off)[0]
        gen = struct.unpack_from("<I", buf, abs_off + 2)[0]
        typ = buf[abs_off + 8]
        hl = struct.unpack_from("<H", buf, abs_off + 9)[0]
        head = struct.unpack_from("<%dH" % (hl // 2), buf, abs_off + 11) if hl else ()
        q = abs_off + 11 + hl
        bl, wlen = struct.unpack_from("<HH", buf, q)
        wbytes = buf[q + 4:q + 4 + wlen]
        n = (bl - 4 - wlen) // 2
        trail = struct.unpack_from("<%dH" % n, buf, q + 4 + wlen + 2) if n > 0 else ()
        return gen, tag, typ, wbytes, head, trail, (q + 2 + bl - abs_off)

    def records(self):
        p = self.arena_off
        end = self.arena_off + self.arena_used
        while p < end:
            yield (p,) + self.record_at(p)
            p += self.record_at(p)[6]


def read_container(path=None):
    path = path or usr_bin_path()
    with open(path, "rb") as fh:
        return Container(fh.read())


# ---------------------------------------------------------------- export


def export(path=None):
    """Return (records, stats); records match the WeType adapter's shape.

    `n` is the save generation the entry was last written in - a recency weight,
    not a hit count (see the module docstring).
    """
    path = path or usr_bin_path()
    c = read_container(path)
    records = []
    gens = set()
    tags = {}
    types = {}
    latin = 0
    split = 0
    best = {}
    collapsed = 0
    for off, gen, tag, typ, wbytes, head, trail, _rl in c.records():
        word = wbytes.decode("utf-16-le", "replace")
        ids = head if len(head) == len(word) else trail
        if head != trail:
            split += 1
        gens.add(gen)
        tags[tag] = tags.get(tag, 0) + 1
        types[typ] = types.get(typ, 0) + 1
        if any(ch.isascii() and ch.isalpha() for ch in word):
            latin += 1
        rec = {
            "w": word,
            "py": pinyin_for(ids),
            "n": gen,
            "t": 0,
            "raw": gen,
            "kind": "user",
            "src": "sogou",
            "count_known": False,
        }
        k = (rec["w"], rec["py"])
        prev = best.get(k)
        if prev is None:
            best[k] = rec
        else:
            collapsed += 1
            if gen > prev["n"]:
                best[k] = rec
    records = list(best.values())
    stats = {
        "file": path,
        "records": len(records),
        "header_count": c.count,
        "index_slots": c.slots,
        "index_free": c.slots - c.count,
        "arena_used": c.arena_used,
        "arena_size": c.arena_size,
        "arena_free": c.arena_size - c.arena_used,
        "gens": len(gens),
        "max_gen": c.gen,
        "gen_sum": c.gen_sum,
        "format_version": "%08d" % c.version,
        "syllables": len(syllable_table()),
        "latin_words": latin,
        "head_trail_mismatch": split,
        "duplicates_collapsed": collapsed,
        "arena_records": c.count,
        "tag_histogram": dict(sorted(tags.items())),
        "type_histogram": dict(sorted(types.items())),
        "has_count": False,
        "has_timestamp": False,
        "n_semantics": "save generation (recency), not a hit count",
        "writable": "append-only: new words can be added, counts cannot",
    }
    return records, stats


# ------------------------------------------------------------ write back


def build_record(gen, ids, wbytes, tag=NEW_REC_TAG, typ=NEW_REC_TYPE):
    """Serialise one arena record."""
    hl = len(ids) * 2
    body = 4 + len(wbytes) + hl
    out = bytearray(13 + hl + body)          # 8 prefix + 1 type + 2 hl + hl + 2 bl + bl
    struct.pack_into("<H", out, 0, tag)
    struct.pack_into("<I", out, 2, gen)
    struct.pack_into("<H", out, 6, 1)
    out[8] = typ
    struct.pack_into("<H", out, 9, hl)
    struct.pack_into("<%dH" % len(ids), out, 11, *ids)
    q = 11 + hl
    struct.pack_into("<H", out, q, body)
    struct.pack_into("<H", out, q + 2, len(wbytes))
    out[q + 4:q + 4 + len(wbytes)] = wbytes
    struct.pack_into("<H", out, q + 4 + len(wbytes), hl)
    struct.pack_into("<%dH" % len(ids), out, q + 6 + len(wbytes), *ids)
    return bytes(out)


def write_entries(entries, dry_run=True, path=None):
    """Append-only merge of (pinyin, word, count, ts) tuples.

    Existing entries are never modified or removed.  A new record is appended to
    the arena and its arena offset is merged into the sorted index; duplicates
    (same word + same pinyin ids) are reported as "exists" and skipped, so the
    call is idempotent.  `count` and `ts` are accepted for interface parity with
    the WeType adapter but are not persisted - SGPU has neither a counter nor a
    timestamp, so a merged word simply becomes a normal entry of the current
    generation.

    Returns (plan, seq) like the WeType adapter.
    """
    path = path or usr_bin_path()
    with open(path, "rb") as fh:
        buf = bytearray(fh.read())
    c = Container(bytes(buf))

    seen = set()
    for _off, _gen, _tag, _typ, wbytes, head, trail, _rl in c.records():
        seen.add((bytes(wbytes), head if head else trail))

    gen_new = c.gen
    plan = []
    pending = []
    batch = set()
    for item in entries:
        py, word = item[0], item[1]
        key = "%s/%s" % (word, py)
        tokens = [t for t in py.replace(",", " ").split() if t]
        if not word.isascii() and len(tokens) != len(word):
            plan.append({"key": key, "status": "length-mismatch"})
            continue
        ids = ids_for(py, word)
        if ids is None:
            plan.append({"key": key, "status": "unknown-syllable"})
            continue
        wbytes = word.encode("utf-16-le")
        if len(wbytes) // 2 != len(ids):
            plan.append({"key": key, "status": "length-mismatch"})
            continue
        sig = (wbytes, ids)
        if sig in seen:
            plan.append({"key": key, "status": "exists"})
            continue
        if sig in batch:
            plan.append({"key": key, "status": "dup-in-batch"})
            continue
        batch.add(sig)
        pending.append((ids, wbytes, key))

    built = [(ids, key, build_record(gen_new, ids, wbytes))
             for ids, wbytes, key in pending]
    need = sum(len(rec) for _i, _k, rec in built)

    if len(pending) > c.slots - c.count:
        for _ids, key, _rec in built:
            plan.append({"key": key, "status": "index-full"})
        return plan, c.count

    grow = c.arena_used + need - c.arena_size
    if grow > 0:
        buf.extend(b"\x00" * grow)

    new_offsets = []
    p = c.arena_off + c.arena_used
    for ids, key, rec in built:
        buf[p:p + len(rec)] = rec
        new_offsets.append((ids, p - c.arena_off))
        p += len(rec)
        plan.append({"key": key, "status": "ok", "len": len(rec)})

    # rebuild the sorted index (engine sorts by the record's trailing id list)
    keys = []
    for slot in range(c.count):
        off = struct.unpack_from("<I", buf, c.idx_off + slot * 4)[0]
        rec = c.record_at(c.arena_off + off)
        # the engine orders the index by the record's LEADING id list
        # (verified: original index is 33459/33459 monotonic on head but has a
        # trail violation), so sort on head and fall back to trail.
        keys.append((rec[4] if rec[4] else rec[5], off))
    merged = sorted(keys + new_offsets, key=lambda x: x[0])
    for slot, (_ids, off) in enumerate(merged):
        struct.pack_into("<I", buf, c.idx_off + slot * 4, off)

    count = c.count + len(pending)
    new_used = c.arena_used + need
    struct.pack_into("<I", buf, O_COUNT, count)
    struct.pack_into("<I", buf, O_ARENA_USED, new_used)
    # O_NONCE is a content checksum the engine validates on load: it fails
    # closed by deleting the file.  Empirically nonce = base + arena_used +
    # count, so updating it as a delta keeps whatever base the file was born
    # with.  Verified against two engine-written saves (delta +58 == +56 bytes
    # + 2 records).
    struct.pack_into("<I", buf, O_NONCE,
                     (c.nonce - c.arena_used - c.count + new_used + count) & 0xFFFFFFFF)
    struct.pack_into("<I", buf, O_ARENA_SIZE, c.arena_size + max(0, grow))
    struct.pack_into("<I", buf, O_GENSUM, c.gen_sum + gen_new * len(pending))
    struct.pack_into("<I", buf, O_SIZE, len(buf))

    if not dry_run and pending:
        backup = path + ".pre-imefuse"
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        tmp = path + ".imefuse.tmp"
        with open(tmp, "wb") as fh:
            fh.write(buf)
        # keep the original mode / owner / xattrs, the engine's loader is picky
        try:
            shutil.copystat(path, tmp)
        except OSError:
            pass
        shutil.move(tmp, path)
    return plan, count


def check():
    """Validate the SGPU container version and index ordering."""
    info = {"store": "SGPU 容器 · 音节表下标 · 有序索引", "compatible": False, "detail": ""}
    try:
        path = usr_bin_path()
        buf = util.read_bytes(path)
    except (OSError, FileNotFoundError) as exc:
        info["detail"] = str(exc)
        return info
    if buf[:4] != MAGIC:
        info["detail"] = "魔数不是 SGPU：%r" % (buf[:4],)
        return info
    c = Container(buf)
    if c.version != 20160521:
        info["detail"] = "容器版本 %d 未适配（已知 20160521）" % c.version
        return info
    offs = c.index()[:c.count]
    prev = None
    bad = 0
    for off in offs:
        try:
            r = c.record_at(c.arena_off + off)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        key = r[4] if r[4] else r[5]
        if prev is not None and key < prev:
            bad += 1
        prev = key
    if bad > max(2, c.count // 1000):
        info["detail"] = "索引/记录校验失败 %d 处，疑似版本变更" % bad
        return info
    info["compatible"] = True
    info["detail"] = "容器 20160521 · %d 条 · arena %d/%d" % (c.count, c.arena_used, c.arena_size)
    return info
