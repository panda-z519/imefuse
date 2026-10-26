# 豆包输入法 (DoubaoIme 1.0.3) 个人词库格式逆向

状态：**格式已完全破解**（导出可用、写回可用、字节级 round-trip 验证通过）。
唯一没做的是「把改后的文件交给引擎自己加载一遍」的权威验收 —— 见最后一节。

---

## 0. TL;DR

| 项 | 结论 |
|---|---|
| 个人词库文件 | `EngineUserDictAccounts/<sha256(uid)>/usr.dat` |
| 目录名 | `sha256(uid)`，uid 从 MMKV `.wave.account.sync/wave.account.userdict.owner` 读 |
| 容器 | 定长头 + 140B 统计区 + 2500×u32 索引 + 80000B 记录区（预分配，不增长） |
| 记录 | `attr(16B) + u16 key_len + key + u16 value_len + value` |
| key | 每个音节一个 **u16 音节码**，`code = 36 + 排序音节表下标` |
| value | 加密后的 `拼音(去分隔) + 词(UTF-16)`，密码是**逐字节/逐 u16 的移位异或** |
| 词频 / 时间 | `attr` 里 `u32 freq` / `u32 ptime` |
| 中文为什么扫不到 | 词按 **UTF-16** 存，且整体加密 → 扫 UTF-8/GBK 必然为空 |
| 导出 | ✅ `imefuse/adapters/doubao.py: export()` |
| 写回 | ✅ `write_entries(entries, dry_run)`，只增不减 |
| 字节级验证 | ✅ 解析→重新序列化，与原文件 **90204 字节完全一致** |

---

## 1. 数据落点（实测）

```
~/Library/Application Support/DoubaoIme/
├── .wave.account.sync/wave.account.userdict.owner      MMKV，含 {"loggedIn":{"uid":"1805814032244244"}}
├── EngineUserDictAccounts/
│   └── 7e81737fc028a4b33d6c2815e292aed32d1667548a239ef53275b2868edf109f/   = sha256("1805814032244244")
│       ├── usr.dat                    ← 个人词库（本文主角）
│       ├── usr_side_dict.dat          辅码/音节表，42059B，**有内容但只有拼音音节**，不是用户词
│       ├── usr_bigram.dat             用户二元组
│       ├── usr_history_bigram.dat
│       ├── usr_enhance.dat            用户增强层
│       ├── usr_url.dat / usr_emailaddress.dat
│       ├── asr_user_ner_dict.dat
│       └── engine_storage.leveldb     「常用语」记录库，与 usr.dat 无关
├── EngineUserDict/                    （空）
└── EngineSync/1805814032244244/sync/sync_state
```

验证目录名：

```python
hashlib.sha256(b"1805814032244244").hexdigest()
# 7e81737fc028a4b33d6c2815e292aed32d1667548a239ef53275b2868edf109f
```

### `engine_storage.leveldb` 是干扰项

只有 12 个 key，全是元数据，没有一条记录：

```
\x00/db_format_version                      = 0x31
\x00/module/\x00\x01/active_generation      = 1
\x00/module/\x00\x01/schema_version         = 13
\x10\x00\x01\x00\x00\x00\x01/meta/next_record_id  = 1     ← 记录数为 0
\x10\x00\x01\x00\x00\x00\x01/meta/prime_count     = 0
... (common_phrase_* 系列)
```

对应 `oime_engine::pinyin::LevelDBCommonPhraseStore`，是「常用语/短语」，
`UsrDict` 走的是 `.dat` 容器，不走 leveldb。

---

## 2. usr.dat 容器格式

所有整数 **little-endian**。

### 2.1 定长头（64 字节）

| 偏移 | 类型 | 值（本机） | 含义 |
|---|---|---|---|
| 0x00 | u32 | 12001 | dict_id（usr.dat 固定 12001） |
| 0x04 | u32 | 20230630 | build_date，十进制 YYYYMMDD |
| 0x08 | u32 | 64 | header_size |
| 0x0c | u32 | 90204 | file_size（预分配，不随记录增长） |
| 0x10 | u32 | 64 | ext_offset |
| 0x14 | u32 | 140 | ext_size |
| 0x18 | u32 | 204 | index_offset = 64 + 140 |
| 0x1c | u32 | 10000 | max_records（建议值） |
| 0x20 | u32 | 2500 | index_entries = **硬容量（记录数上限）** |
| 0x24 | u32 | 2 | record_count |
| 0x28 | u32 | 10204 | data_offset = 204 + 2500×4 |
| 0x2c | u32 | 80000 | data_capacity（字节） |
| 0x30 | u32 | 114 | data_used（字节） |
| 0x34 | u32 | 2 | key 长度字段宽度 |
| 0x38 | u32 | 2 | value 长度字段宽度 |
| 0x3c | u32 | 16 | attr 长度（固定） |

**文件大小校验**：`64 + 140 + 2500*4 + 80000 = 90204` ✅ 完全对上。

> 之前 `usr.dat` 被记成 90184 是记错了 —— 90184 是 `asr_user_ner_dict.dat`。
> 两个文件的 ext_size 差 20（140 vs 120），所以总大小正好差 20。

### 2.2 扩展统计区（140 字节 @ 0x40）

由 `UsrDict::Add` 维护，`GetFreqSumAndPtimeSum` / `GetSingleCharCandAndAllCandCount` 读它：

| 偏移 | 含义 |
|---|---|
| +0x00 | max_pseudo_time（最大伪时间，Add 时 +1） |
| +0x04 | last_update_time（最近一次 Add 的 time()） |
| +0x08 | freq_sum（每次 Add +1） |
| +0x0c | 单音节词条数 |
| +0x10 | pseudo_time_sum（每次 Add += 该条的 pseudo_time） |
| +0x14.. | 全 0 |

本机实测 `02 | ts | 02 | 00 | 03`，与两条记录 `pseudo=1,2 / freq=1,1` 完全吻合
（max=2，freq_sum=2，pseudo_sum=1+2=3）。

### 2.3 索引数组（2500 × u32 @ 0xCC）

`index_array[i] = 第 i 条记录在数据区内的字节偏移`。
本机：`[0, 57, 0, 0, ...]`（rec0 @0，rec1 @57）。

### 2.4 记录（数据区 @ 0x27DC）

```
+0x00  16B attr:
        u32 freq         词频
        u32 pseudo_time  伪时间（单调递增，Add 时 max+1）
        u32 ptime        真实时间戳（time()）
        u16 flags        WordInfoFlagType
        u16 pad
+0x10  u16 key_len       （字节）
+0x12  key               key_len 字节，n 个 u16 音节码
       u16 value_len     （字节）
       value             value_len 字节
```

单条大小 = `16 + 2 + key_len + 2 + value_len`。

本机两条记录：

```
rec0 @0   attr = {freq=1, pseudo=1, ptime=1793006100, flags=0}
          key_len=10  key = 9a00 3600 3e00 5001 5400      -> [154, 54, 62, 336, 84]
          value_len=27
rec1 @57  attr = {freq=1, pseudo=2, ptime=1793006102, flags=0}
          key_len=10  key = 5201 3901 7800 2401 9201      -> [338, 313, 120, 292, 402]
          value_len=27
```

**反汇编依据** —— `UsrDictBase::UsrDictBaseAssist::WriteKeyAttrValue(this, index, key, value, attr)`
（OimeEngine arm64，符号 `__ZN11oime_engine11UsrDictBase17UsrDictBaseAssist17WriteKeyAttrValue...`）
逐条还原成：

```
memcpy(data + used,             attr.data,  header[0x3c]=16)
memcpy(data + used + 16,        &key_len,   header[0x34]=2)
memcpy(data + used + 18,        key.data,   key.size)
memcpy(data + used + 18+klen,   &val_len,   header[0x38]=2)
memcpy(data + used + 20+klen,   value.data, value.size)
index_array[index] = used
header[0x24] += 1        // record_count
header[0x30]  = used + 16+2+klen+2+vlen
```

---

## 3. value 的加密（这是「扫不到中文」的真因）

`UsrDict::EncryptInputWord(pinyin, word_utf16)`：

```
value = <u16 plen><cipher_pinyin[plen]><u16 wlen><cipher_word[wlen]>
```

密码本体（`oime_engine::EncryptChar` / `EncryptChar16` / `EncryptStr` / `EncryptU16Str`）：

```asm
EncryptChar:    xor  $0xA5, %dil        ; c ^ 0xA5
                rol  $5,   %dil         ; 再循环左移 5
DecryptChar:    rol  $3,   %dil
                xor  $0xA5, %dil
EncryptChar16:  xor  $0x65C9, %edi
                rol  $9,   %di
```

即：

```python
enc8(c)  = rol8 (c ^ 0xA5, 5)      dec8(e)  = rol8 (e, 3) ^ 0xA5
enc16(c) = rol16(c ^ 0x65C9, 9)    dec16(e) = rol16(e, 7) ^ 0x65C9
```

- `cipher_pinyin` 是**去掉分隔符**的拼音串（`"hebingceshici"`），每字节一换。
- `cipher_word` 是词的 **UTF-16LE 码元**，每 u16 一换。

解密本机两条 value：

```
0d 00 | b9 18 f8 99 79 58 d8 18 da b9 99 d8 99 | 05 00 | 6382 777e 1104 dc39 1dd0
       → "hebingceshici"                                 → "合并测试次"

0d 00 | da b9 1a fa 1a 78 98 9a 99 98 79 9b 99 | 05 00 | d4b5 6958 1238 d411 3864
       → "shurufaqianyi"                                 → "输入法迁移"
```

> 注：`plen=13` 不是 5，是因为拼音是「音节码拼起来的原始串」，长度随词长变化；
> `wlen=5` 是词的 UTF-16 码元个数（5 个汉字）。两者都自洽。

---

## 4. key 的音节码

**公式：`code = 36 + 排序拼音音节表下标`**，`0..35` 为保留码。

音节表就在 `OimeEngine` 二进制里（连续 NUL 分隔的字符串，文件偏移 `0x7a4b5f`），
413 条，含裸音节 `m` / `n`：

```
a ai an ang ao ba bai ban bang bao bei ben beng bi bian biao bie bin bing bo bu
ca cai can cang cao ce cen ceng cha chai chan chang chao che chen cheng chi ...
... lv m ma ... n na ... zuan zui zun zuo
```

10 个实测样本全部命中：

| 音节 | 表中下标 | 计算 code | 文件里的 code |
|---|---|---|---|
| bing | 18 | 54 | 54 ✅ |
| ce | 26 | 62 | 62 ✅ |
| ci | 48 | 84 | 84 ✅ |
| fa | 84 | 120 | 120 ✅ |
| he | 118 | 154 | 154 ✅ |
| qian | 256 | 292 | 292 ✅ |
| ru | 277 | 313 | 313 ✅ |
| shi | 300 | 336 | 336 ✅ |
| shu | 302 | 338 | 338 ✅ |
| yi | 366 | 402 | 402 ✅ |

**这就是写回的关键**：有了这张表，任意拼音 → 音节码序列 → key，不需要引擎。

### 排序规则

`UsrDict::KeyCompare`（反汇编 `ldrh` 逐 u16 比较）：

1. 按 key 的 **u16 数值**字典序比较；
2. 前缀相同则短的在前；
3. key 相同再比 value 的**原始字节**（`ValueCompare` = memcmp）。

注意**不是**按 key 原始字节序 —— 例：`9a 00…`(154) 排在 `52 01…`(338) 前面。

---

## 5. 落盘时机（重要，也是唯一没吃透的地方）

| 观测 | 结果 |
|---|---|
| 开工时 `usr.dat` | 90204B，**除 72 字节头外全 0**，`record_count=0` |
| 10 个词（你好/师姐/测试/输入法/豆包…） | **磁盘上任何位置都没有**，`grep -r nihao/shijie` 全空 |
| `pkill -TERM` + 重启 | leveldb 被 compact，`usr.dat` 里出现 **2 条**记录（`合并测试次`、`输入法迁移`），`ptime≈17:15:00` |
| 之后再用 osascript 真实上屏 `nihao/ceshi/wo/doubao/shurufa` | 文本确实进了 TextEdit（`你好测试我豆包输入法你好我输入法`），但 `usr.dat` 的 `record_count` 一直停在 2，60 秒轮询无变化 |
| 第二次 `pkill -TERM` | 仍无新记录，mtime 不动 |

结论：
- 词库**确实存在 usr.dat 里**，且能被导出（已实测解出两条真实词）；
- 但引擎是**懒落盘**：缓存在内存，只在某些时机（重启/上下文切换/内部定时）写一次，
  `SIGTERM` 也不保证 flush。**brief 里那 10 个词在开工前就没写下去过**，
  我复现的两次上屏同样没写下去 —— 所以「导出验证」用的是引擎自己写出来的 2 条真实记录。
- 要稳定拿数据：敲完词后**优雅退出豆包**（或换输入法/切应用触发保存），再读文件。

> 排查过但不是词库的：`EngineUserDict/`（空）、`.wave.account.sync`（只有账号态）、
> `Log/alog/*`（加密日志，strings 只有噪声）、`Parfait/*`（崩溃上报）、
> `usr_side_dict.dat`（音节表，全文件非零但没有用户词）。

---

## 6. 适配器自测（真实输出）

`imefuse/adapters/doubao.py`，接口对齐 `wetype.py`。

### 6.1 容器 round-trip（最强证据）

```
header: e1 2e 00 00 | e6 b1 34 01 | 40 00 00 00 | 5c 60 01 00 | 40 00 00 00 | 8c 00 00 00
        cc 00 00 00 | 10 27 00 00 | c4 09 00 00 | 02 00 00 00 | dc 27 00 00 | 80 38 01 00
        72 00 00 00 | 02 00 00 00 | 02 00 00 00 | 10 00 00 00
ext:    02 00 00 00 16 1a df 6a 02 00 00 00 00 00 00 00 03 00 00 00 ...
count 2 used 114 idx[0:3] [0, 57, 0]
records: [(0,  57, ('hebingceshici',  '合并测试次'), 1, 1, 1793006100),
          (57, 57, ('shurufaqianyi', '输入法迁移'), 1, 2, 1793006102)]
roundtrip equal: True len 90204 90204
```

> 解析 → `build_image()` 重新序列化 → **与引擎原文件逐字节相同**。
> 这证明头、统计区、索引数组、记录布局、attr 语义全部建模正确。

### 6.2 `export()`

```json
[
  {"w": "合并测试次", "py": "he bing ce shi ci", "n": 1, "t": 1793006100,
   "raw": "0d00b918f8997958d818dab999d89905006382777e1104dc391dd0",
   "kind": "user", "src": "doubao",
   "_pseudo": 1, "_flags": 0, "_codes": [154,54,62,336,84], "_py_raw": "hebingceshici"},
  {"w": "输入法迁移", "py": "shu ru fa qian yi", "n": 1, "t": 1793006102,
   "raw": "0d00dab91afa1a78989a9998799b990500d4b569581238d4113864",
   "kind": "user", "src": "doubao",
   "_pseudo": 2, "_flags": 0, "_codes": [338,313,120,292,402], "_py_raw": "shurufaqianyi"}
]
```

```json
{
  "path": ".../EngineUserDictAccounts/7e8173.../usr.dat",
  "dict_id": 12001, "build_date": 20230630, "file_size": 90204,
  "record_count": 2, "data_used": 114, "data_capacity": 80000,
  "index_entries": 2500, "freq_sum": 2, "pseudo_time_sum": 3,
  "last_update": 1793006102, "single_syllable": 0, "undecodable": 0
}
```

`py` 直接用 key 的音节码查表得到（`_py_raw` 是 value 里解出的无分隔拼音），
两者互为交叉校验。

### 6.3 `write_entries()`（沙箱副本，未碰真实目录）

输入：

```python
[{"py":"ni hao","w":"你好","n":3,"t":1793007000},
 {"py":"ce shi","w":"测试","n":2,"t":1793007001},
 {"py":"he bing ce shi ci","w":"合并测试次","n":9,"t":1793007002},   # 已存在
 {"py":"zzz","w":"坏拼音","n":1,"t":1793007003}]                    # 非法音节
```

结果：

```
status=add        w=你好            key=ff009900  value=05007999b99859020055527968
status=add        w=测试            key=3e005001  value=0500d818dab99902001104dc39
status=exists     w=合并测试次       reason=word already in dict (add-only, not touched)
status=bad-pinyin w=坏拼音           reason=unknown pinyin syllable: 'zzz'

count 2 -> 4   used 114 -> 188   size 90204
old records preserved byte-identically: True
   ('ceshi',        '测试')      freq 2 pseudo 4 ptime 1793007001 off 0
   ('hebingceshici','合并测试次') freq 1 pseudo 1 ptime 1793006100 off 37
   ('nihao',        '你好')      freq 3 pseudo 3 ptime 1793007000 off 94
   ('shurufaqianyi','输入法迁移') freq 1 pseudo 2 ptime 1793006102 off 131
ext: 04 00 00 00 b7 1c df 6a 07 00 00 00 00 00 00 00 0a 00 00 00
idempotent re-serialize: True
```

- 旧记录 **attr/key/value 字节完全不变**，只是位置按排序后重排；
- 新记录按 `(key, value)` 有序插入，索引数组与 `data_used` 同步重建；
- 已存在的词直接跳过，**绝不覆盖**（这正是「只增不减」）；
- 输出文件仍是 90204 字节（预分配，不增长）；
- 对输出再解析、再序列化 → 幂等。

---

## 7. 写回算法（实现要点）

1. 解析现有 `usr.dat` → 全部 `Record`（原样保留 attr/key/value 字节）；
2. 对每条待合并词条：
   - 词已存在 → `exists`，跳过（add-only）；
   - 拼音有未知音节 → `bad-pinyin`，跳过；
   - 否则构造 `Record`：
     `key = pack("<%dH", *[36+idx(s) for s in syllables])`，
     `value = enc8(pinyin_join) / enc16(word)`，
     `attr = {freq, max_pseudo+1, ptime, flags=0}`；
3. `records = 旧 + 新`，按 `key_value_compare` 稳定排序（旧记录相对顺序不变）；
4. 重建：`index_array[i] = offset`、`record_count`、`data_used`、
   扩展统计区（max_pseudo / freq_sum / single_syllable / pseudo_sum / last_update）；
5. 写 `path + ".imefuse-tmp"` → `os.replace()`；首次写入留 `path + ".imefuse-bak"`。

容量红线：`record_count ≤ index_entries(2500)`、`data_used ≤ data_capacity(80000)`，
超出直接抛错而不是截断。

---

## 8. 卡点 / 未完成

1. **没有做引擎侧加载验收**。
   `PyDictManager::DictRef<UsrDict>()` 在二进制里只有 guard 变量、没有可 dlsym 的函数体，
   构造 `UsrDict` 又要摆 vtable，成本过高，所以没写 C++ harness。
   目前的证据是「字节级 round-trip 完全一致」+「写回完全照抄 `WriteKeyAttrValue` 的布局」，
   强度很高但不是 100%。

   **安全验收配方（需要真实目录权限，我没做）**：
   ```bash
   # 1. 整目录备份
   rsync -a "$HOME/Library/Application Support/DoubaoIme/" /tmp/doubao-bak/
   # 2. 用适配器写回（会自己留 .imefuse-bak）
   python3 -c "import sys;sys.path.insert(0,'.');from imefuse.adapters import doubao as d;print(d.write_entries([{'py':'ni hao','w':'你好','n':3,'t':1793007000}],dry_run=False))"
   # 3. 优雅退出再拉起豆包，随便敲 nihao，看「你好」是否被提前/加权
   # 4. 不满意就 rsync 回去
   ```

2. **落盘时机没定死**（第 5 节）。目前只知道懒写、`SIGTERM` 不可靠。
   要工程化「敲完就能导」，得找出触发保存的钩子 —— 候选是
   `OimeEngine.exportUserDict(dir:)` / `saveUserDict(sync:)` /
   `reloadUsrDictForAllRunningEngines(path:)`（这三个都是**导出符号**，
   见 `nm -gU` 里的 `_$s10OimeEngineAAC12saveUserDict4syncS2b_tF` 等）。
   下一步若要彻底解决，写个 Swift/ObjC 小程序调这几个 API 比继续读反汇编划算。

3. **`0..35` 这 36 个保留音节码含义未知**（单字母/标点？不影响导出与写回）。

4. `usr_side_dict.dat`、`usr_bigram.dat`、`usr_enhance.dat` 没做。
   前者的头布局与 usr.dat **不同**（48 字节头 + 1025 桶 + 786 条 + 三段偏移），
   是独立的 `UsrSideDict` 类，不是用户词。

---

## 9. 关键符号索引（OimeEngine.framework）

```
oime_engine::pinyin::UsrDict
  ├── Add(vector<u16> const&, string const&, string<char16_t> const&, WordInfoFlagType)
  ├── EncryptInputWord(string const&, string<char16_t> const&)
  ├── DecryptInputWord(Slice const&, string&, KFixArray<char16_t,64>&)
  ├── Export() const / ExportDictToString() const
  ├── Load / OnSave / OnCreateEmptyDict / Rollback
  ├── KeyCompare / ValueCompare / AttributeCompare
  └── GetFreqSumAndPtimeSum(int&,int&) / GetMaxPtime() / GetLastUpdateTimestamp()
oime_engine::UsrDictBase          ← 全部是导出符号（T），可 dlsym
  ├── LoadDict(string const&) / Save(bool, EventTracker*) / Merge(string const&)
  ├── Insert(Slice,Slice) / Insert(Slice,Slice,Slice,EventTracker*)
  ├── GetAttribute(u32) / GetAttrKeyValue(u32) / GetExtendedArea()
  ├── LowerBoundByKey / UpperBoundByKey / GetIndexRangeByKey / KeyValueCompare
  ├── UsrDictBaseAssist::WriteKeyAttrValue(...)   ← 记录布局在这里
  └── CreateEmptyDict / Expand / Reduce / Clear / Unload
oime_engine::MergeLocalUsrDict(string const&, string const&)   ← 免费函数，可 dlsym
oime_engine::EncryptChar / DecryptChar / EncryptChar16 / EncryptStr / EncryptU16Str
OimeEngine（Swift，导出）
  ├── exportUserDict(dir:)
  ├── saveUserDict(sync:) / saveAllRunningUserDicts(sync:)
  ├── mergeLocalUsrDict(srcPath:dstPath:) -> Bool
  ├── reloadUsrDictForAllRunningEngines(path:)
  └── setInputMode(_:userDictPath:)
```

反汇编/符号产物都在 `work/doubao-notes/`：
`engine-strings.txt`、`engine-syms.txt`、`globals.txt`、`disasm-arm64.txt`。
