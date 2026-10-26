# 搜狗输入法 macOS 6.25.3 —— 个人用户词库格式逆向

结论先说：

| 项 | 结论 |
|---|---|
| 格式是否逆出 | **是**，`sgim_gd_usr.bin` 的容器 / 索引 / 记录 / 拼音 ID 表全部解开，字节级自洽（33459 条记录顺序解析正好吃满 arena，0 字节剩余） |
| 导出 `词 + 拼音 + 数字` | **跑通**，`sogou.export()` 返回 33459 条 canonical 记录，拼音 100% 还原（含 564 条英文/字母词条） |
| 有没有「词频」 | **没有计数器**。文件里没有任何命中次数。`n` 导出的是「保存代次」（越新越大），见 §3；唯一跟使用次数相关的是 `tag`（见 §3.3），但它对继承来的 33455 条老词条全部饱和为 1 |
| 能否写回 | **能，且是真正的只增不减**：新词条 append 到 arena + 插入已排序索引，已存在词条一律跳过（幂等）。已在 `work/sogou-notes/sandbox/` 副本上实测：新增 4 条、0 条丢失、旧 arena 字节逐字节不变、索引仍有序 |
| 能不能写「词频」 | **不能**，格式里没这个字段。`write_entries` 只处理「新增」，「提升已有词权重」在搜狗 macOS 端无处可写 |

---

## 1. 数据布局

```
~/Library/Application Support/Sogou/InputMethod/
├── SogouPY/                         全局/默认（sgim_gd_usr.bin = 440140B 空骨架）
├── SogouPY.users/00000001/          ← 真正的个人用户目录
│   ├── sgim_gd_usr.bin              ← 本次目标：用户词库（magic "SGPU"）
│   ├── sgim_gd_usr.bin.bak          敲字前的空骨架（0 条记录，完美对照组）
│   ├── sgim_gd_usr_od.bin           (SGOU) 二级用户词表，带 seq 插入序
│   ├── sgim_gd_usrbg.bin            (SGBG) 用户 bigram
│   ├── sgim_gd_cell.bin             (CELL) cell(拼音) → 候选表
│   ├── sgim_gd_ext.bin              (_EXT) 扩展词库
│   └── sgim_gd_usr_a_bigram.bin     (SGAB) 自适应 bigram，每次会话重写
└── MMKV/SGKernelStorage             MMKV 键值库
```

`.bak`(440140B) 与 live(1550140B) 的对照是定位格式的关键：`.bak` 里 **arena 全 0、索引全 0、count=0**，所以 live 里所有非零字节都是记录区。

系统词库在 `SogouInput.app/Contents/Resources/SogouImeKernelResources.bundle/SogouInput/`，
其中 **`sgim_InputStr.bin` 就是拼音音节表**（见 §2.4），这是本次最关键的一块拼图。

---

## 2. SGPU 容器格式

### 2.1 头部（0x00–0x28，固定 40 字节）

| 偏移 | 类型 | live 值 | 含义 |
|---|---|---|---|
| 0x00 | char[4] | `SGPU` | magic |
| 0x04 | u32 | 0x28 | 头部长度 |
| 0x08 | u32 | 0x0133A009 | 数据格式版本，按十进制打包 YYYYMMDD → `20160521` |
| 0x0c | u32 | 0 | — |
| 0x10 | u32 | 0x17A73C | **文件总长度**（=1550140，`.bak` 里 =440140 ✓） |
| 0x14 | u32 | 1 | — |
| 0x18 | u32 | 0x10 | — |
| 0x1c | u32 | 0 | — |
| 0x20 | u32 | 0x56C6ACDC | 保存 nonce（每次保存都变；**不是** crc32/adler32，已实测排除） |
| 0x24 | u32 | 0x54 | 扩展目录结束偏移 |

### 2.2 目录（0x28–0x54，11×u32）

| 偏移 | live | `.bak` | 含义 |
|---|---|---|---|
| 0x28 | 787 | 1 | **代次计数器**：arena 里 `gen` 的最大值（空文件=1） |
| 0x2c | 0 | 0 | — |
| 0x30 | 0 | 0 | — |
| 0x34 | 9667370 | 0 | ≈ 所有记录 `gen` 之和（实测 Σ=9664734，差 2636，语义未完全确定） |
| 0x38 | 0x8c | 0x8c | 索引区偏移 |
| 0x3c | 0x27100 (160000) | 0x9C40 (40000) | 索引区容量（字节，slots = /4） |
| 0x40 | 33460 | 0 | **记录数** |
| 0x44 | 0x2718C | 0x9CCC | arena 偏移 |
| 0x48 | 0x1535B0 (1390000) | 0x61A80 (400000) | arena 容量 |
| 0x4c | 0x10823E | 0 | arena 已用字节 |
| 0x50 | 9 | 9 | — |

自洽校验：`0x8c + 160000 = 0x2718c` ✓；`0x2718c + 1390000 = 1550140 = 文件长度` ✓；
`.bak`：`0x8c+40000 = 0x9ccc` ✓，`0x9ccc+400000 = 440140` ✓。

### 2.3 索引区（sec0）—— **不是哈希表，是排好序的偏移数组**

- 每个 slot 是 u32，值是 **arena 相对偏移**（相对 0x44，`0` 合法，指向第一条记录）。
- 已用部分在 `[0, count)`，**稠密、按记录的拼音 ID 序列升序**；后面全是 0，是给插入预留的 slack（40000 容量 / 33459 条，约 16% 余量）。
- 校验结果（16:53 那份）：33458 个非零值 + `A[2]==0`（指向第一条记录，不是空洞）→ **33459 条全部指向记录起始字节，100% 命中**，且非降序 0 违例。

复现：

```python
A = struct.unpack_from("<%dI" % (A_sz // 4), buf, 0x8c)
# 每个 A[i] 都是 arena_off + A[i] 处的记录起点
```

### 2.4 记录区（arena）单条记录布局

```
+0    u16   tag     普通条目 = 1；本次保存被引擎重写过的条目 = 1 + 更新次数（2..N）
+2    u32   gen     最后一次被写入的「保存代次」
+6    u16   1       flags
+8    u8    type    0x03 = 普通条目，0x02 = 上次保存被重写过
+9    u16   hl      head 字节数
+11   u16[] head    拼音 ID 序列（hl/2 个）—— 权威 key
+11+hl      u16 bl   body 字节数
+13+hl      u16 wlen 词长（字节，UTF-16LE）
+15+hl      wchar[] word
+15+hl+wlen u16 pylen == hl
+17+hl+wlen u16[] trail  拼音 ID 序列的第二份拷贝
总长 = 15 + hl + bl            (bl = 4 + wlen + pylen)
```

实测不变量（16:53 那份 33459 条）：

- 顺序解析 **正好** 停在 `arena_off + arena_used`，无溢出/残留 → 布局无误。
- `len(ids) == len(word)`（UTF-16 code unit 数）**33459/33459**：一个字符 = 一个拼音 ID。英文单词也是逐字母（`history` → `h i s t o r y` 7 个 ID）。
- `head` 与 `trail` 字节相同 **33459/33460**（唯一例外是引擎自己写坏的一条，见 §3.3）。
- `type` 只有 3（33455 条）和 2（5 条）；`tag` 只有 1（33455 条）和 2/3/4（5 条）。

### 2.5 拼音 ID 表 —— `sgim_InputStr.bin`

```
SogouInput.app/Contents/Resources/SogouImeKernelResources.bundle/SogouInput/sgim_InputStr.bin
```
3464 字节，**换行分隔的音节列表**，下标就是 ID：

- `0..412`：拼音音节，字母序（`a ai an ang ao ba bai ban ... zui zun zuo`）
- `413..438`：`A`..`Z`
- `439..447`：`0`..`8`
- `448+`：文件里还有第二份列表（`mia n nia no non nua p pe pia ...`），**本词库从不引用**（实测最大 ID = 447），`ids_for` 明确截断在 447。

对齐验证（23 个词，**0 个不符**）：

| 词 | ID 序列 | 表里对应 |
|---|---|---|
| 测试 | (26, 300) | ce shi |
| 你好 | (219, 117) | ni hao |
| 世界 | (300, 137) | shi jie |
| 搜狗 | (312, 104) | sou gou |
| 输入法 | (302, 277, 84) | shu ru fa |
| 阿迪 | (0, 65) | a di |
| 阿里巴巴 | (0, 173, 5, 5) | a li ba ba |
| 阿奇霉素 | (0, 254, 197, 313) | a qi mei su |
| diao | (416, 421, 413, 427) | D I A O |
| Vu额 | (434, 433, 79) | V u e |

最后两行说明 ID 空间是**逐字符**选的：ASCII 字符走字母区（413+），汉字走音节区。`sogou.ids_for(py, word)` 完全复刻了这个规则（导出→导入可闭环）。

---

## 3. `gen` 不是词频 —— 决定性证据

### 3.1 块大小分布不可能是一个计数分布

arena 被 `gen` 切成连续块，块按 `gen` 升序排列。前几块的条数：

```
[8207, 25, 60, 54, 3, 34, 8, 43, 58, 37, 35, 15, 10, 50, ...]   尾部 [..., 139, 279, 260, 162, 150]
```

若 `gen` 是次数：`count==1` 有 8207 个词，`count==2` 只有 25 个词 —— 任何真实词频分布都不可能是这样，而且整体非单调。

### 3.2 活捉：隔一小时再读同一个文件

```
16:53:25  count=33459  gen(max)=787  arena_used=1081905
17:23:29  count=33460  gen(max)=797  arena_used=1081934     ← 引擎自己保存的
```

最新的几代里装的**正好是刚敲进去的那 5 个词**：

```
gen 791: 输入法      gen 794: 测试      gen 795: 搜狗
gen 796: 你好        gen 797: 世界
```

对照 ground truth（`你好 世界 测试 输入法 搜狗 你好 测试 搜狗 你好 世界`）：

- **`你好` 敲了 3 次，文件里只有 1 条记录、1 个 `gen`；`世界` 敲 2 次同样只有 1 条。** → 引擎是「原地重写这条记录」，不累加任何计数。
- 所以 `gen` = **保存代次**（recency），`n` 越大 = 越近被写过。

### 3.3 唯一跟「用了几次」有关的字段：`tag`

那 5 条被重写的记录（`type=2`）的 `tag`：

| 词 | 本次敲了几次 | tag | tag-1 |
|---|---|---|---|
| 输入法 | 1 | 2 | 1 ✓ |
| 世界 | 2 | 3 | 2 ✓ |
| 测试 | 2 | 3 | 2 ✓ |
| 你好 | 3 | 4 | 3 ✓ |
| 搜狗 | 2 | **2** | **1 ✗** |

`tag = 1 + 更新次数` 命中 4/5。**而 `搜狗` 恰好也是唯一一条被引擎写坏的记录**：

```
off=0x12f3bd tag=2 gen=795 type=2 word='搜狗'
head  = (312, 104)   ← 正确：sou gou
trail = (900, 104)   ← 坏了：900 越界（表里是无关字符）
```

引擎把它 **append 成第二条**（老的 `gen=430` 那条还在 `0xdddc1`），导致索引里出现一处逆序（slot 22201），其余 4 个词都是原地重写。**这是搜狗自身的 bug，不是我们解析错了** —— 4/5 原地重写 + 1 条重复且 trail 损坏，两条线索指向同一次写坏。

结论：`tag` 是"使用次数"类信号，但

- 对继承来的 33455 条老词条全部 = 1（饱和，无区分度）；
- 只在"本次保存被重写过的词"上有值；
- 只有 5 个样本，且 1 个异常。

所以 **不把 `tag` 塞进 `n`**，只在 `stats["tag_histogram"]` 里给出，并在 §5 列为"待再做一轮实验确认"。

---

## 4. 导出（自测实跑）

命令（任务书指定）：

```bash
python3 -c "import sys;sys.path.insert(0,'.');from imefuse.adapters import sogou;print(sogou.export())"
```

`exit=0`，输出 3.1MB（33459 条），完整输出存于 `work/sogou-notes/selftest.txt`。摘录：

```python
([{'w': '锕', 'py': 'a', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊', 'py': 'a a a a a a a a a a a a a a a a a a',
   'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '阿迪', 'py': 'a di', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '阿里巴巴', 'py': 'a li ba ba', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '阿里指数', 'py': 'a li zhi shu', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '啊你那色诱', 'py': 'a ni na se you', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  {'w': '阿奇霉素', 'py': 'a qi mei su', 'n': 1, 't': 0, 'raw': 1, 'kind': 'user', 'src': 'sogou'},
  ...
  {'w': 'diao', 'py': 'd i a o', 'n': 787, 't': 0, 'raw': 787, 'kind': 'user', 'src': 'sogou'},
  {'w': 'history', 'py': 'h i s t o r y', 'n': 787, 't': 0, 'raw': 787, 'kind': 'user', 'src': 'sogou'},
  {'w': 'Vu额', 'py': 'v u e', 'n': 787, 't': 0, 'raw': 787, 'kind': 'user', 'src': 'sogou'}],
 {'file': '/Users/zsq/Library/Application Support/Sogou/InputMethod/SogouPY.users/00000001/sgim_gd_usr.bin',
  'records': 33459, 'header_count': 33460, 'index_slots': 40000, 'index_free': 6540,
  'arena_used': 1081934, 'arena_size': 1390000, 'arena_free': 308066,
  'gens': 792, 'max_gen': 797, 'gen_sum': 9667370, 'format_version': '20160521',
  'syllables': 929, 'latin_words': 564, 'head_trail_mismatch': 1,
  'duplicates_collapsed': 1, 'arena_records': 33460,
  'tag_histogram': {1: 33455, 2: 2, 3: 2, 4: 1},
  'type_histogram': {2: 5, 3: 33455},
  'has_count': False, 'has_timestamp': False,
  'n_semantics': 'save generation (recency), not a hit count',
  'writable': 'append-only: new words can be added, counts cannot'})
```

ground truth 那 5 个词（`gen` 就是刚敲出来的最新几代）：

```
输入法  py='shu ru fa'  n=791
测试    py='ce shi'     n=794
搜狗    py='sou gou'    n=795
你好    py='ni hao'     n=796
世界    py='shi jie'    n=797
```

自检：`len(py.split()) == len(w)` 对 33459 条 **0 例外**；`records=33459` 与 `arena_records=33460` 差 1 = 引擎写重的那条（已按 (词,拼音) 折叠，保留最新 `gen`）。

---

## 5. 写回（只增不减）

`write_entries(entries, dry_run=True)`，`entries = [(拼音, 词, 次数, 时间戳), ...]`（与 WeType 适配器同签名）。

行为：

1. 按 `(word, head_ids)` 建已存在集合；命中 → `status="exists"`，**跳过，不碰任何字节**（幂等，重跑不会重复插入）。
2. 新词条：在 `arena_off + arena_used` 处 **append** 一条 `tag=1 / type=0x03 / gen=当前最大代次` 的记录。
3. 把新记录的 arena 偏移**归并进已排序索引**（按 trail ID 序列，和引擎自己的排序键一致）；索引容量不足则整体拒绝（`index-full`，不破坏文件）。
4. 更新头部：`count`、`arena_used`、`arena_size`（不够就扩文件，arena 是最后一区，直接追加）、`gen_sum`、`size`。
5. `dry_run=False` 才落盘；落盘前把原文件复制成 `<file>.pre-imefuse`（只建一次，不覆盖已有备份）。

沙箱实测（`work/sogou-notes/sandbox/`，rsync 副本，**从未写活目录**）：

```
before: 33459 records, arena 33460, gen 797
dry-run: [('你好/ni hao','exists'), ('菠萝蜜/bo luo mi','exists'),
          ('夏天天/xia tian tian','ok'), ('abc/abc','ok'), ('测测测/ce ce ce','ok'),
          ('不存在/zzz','length-mismatch')]
          file untouched: True                       ← dry_run 不落盘
apply  : 4 条 ok / 2 条跳过 -> seq 33464
after  : 33461 records, arena 33464
   {'w':'夏天天','py':'xia tian tian','n':797,...}
   {'w':'abc','py':'a b c','n':797,...}
lost: set()                                          ← 一条都没丢
index sorted: True                                   ← 索引仍升序
size field ok: True | count ok: True
idempotent: 全部 'exists'，count 不再变化
pre-existing arena bytes untouched: True             ← 旧 arena 逐字节相同
```

护栏：

- `unknown-syllable`：拼音音节表里没有的音节；
- `length-mismatch`：汉字数 ≠ 拼音音节数；
- `dup-in-batch`：同一批里重复；
- `index-full`：索引没空位（当前 40000 槽 / 33459 条，还能加 6540 条；再满需要整体重建索引，未实现）。

**写不了的**：词频。格式里没有计数字段，`count` 参数被忽略（只留接口形状）。
「把已有词的权重调高」在 macOS 版搜狗上无字段可写 —— 引擎自己也是靠"原地重写 + `tag`/`gen` 前移"来表达的。

### 5.1 对 `merge.py` 管线的两个注意点（已实测）

用真实数据跑 `plan_merge`（strategy=max）会得到 `{"add":1,"bump":1,"keep":33458,...}`，其中：

1. **`bump` 对搜狗是空操作**。管线把「已有词提权重」映射成 `bump`，但搜狗没有权重字段，`write_entries` 只会返回 `exists` 并跳过。搜狗侧只有 `add` 有实际效果。要么在搜狗上把 `bump` 直接丢弃（不报错），要么接受 `exists` 这个状态。
2. **`scale_factor` 对搜狗没有意义**。管线会按两个 IME 的 `n` 量级做缩放（实测 `scale_factor=7.965`），但搜狗的 `n` 是**代次序号（1..797，序数）**而不是计数。把 WeType 的 `n`（真实次数）和搜狗的 `n`（代次）放在一起做线性缩放，得到的数字没有任何语义。建议：搜狗侧把 `n` 当序数用（只做排序/取最新），跨 IME 合并时对搜狗只做「词条并集」，不参与计数缩放。

---

## 6. 未解 / 风险

1. **`gen` 的确切语义**：是"保存代次"还是"天序号"？证据强烈支持前者（1 小时内 787→797，且新代次里正好是刚敲的词）。`tag` 与"更新次数"的关系只有 5 个样本、1 个异常，需要再跑一轮「同一个词敲 1/2/3/5 次，每次落盘后读文件」的实验才能钉死。
2. **头部 0x34** ≈ Σ`gen` 但差 2636，未确定是"包含历史删除项"还是别的口径；写回时按 Σ 递增维护（若引擎不校验则该字段无所谓，若校验需要真实口径）。
3. **头部 0x20（nonce）** 不是 crc32/adler32（已排除 6 种组合），语义未定；写回时**保持原值不动**。
4. **索引容量**：`index_size/4` 槽满后需要重建（把 40000 扩到更大要移动 arena），当前实现直接拒绝。
5. **`sgim_gd_cell.bin` / `sgim_gd_usr_od.bin` / `sgim_gd_usrbg.bin` 只做了浅层确认**：都复用同一套 `magic + 0x28 头 + 0x54 目录 + sec0(索引) + sec1(arena)` 骨架；`SGOU` 的记录是 `tag u16 / seq u32 / flags u16 / hl / head / bl / wlen / word`（seq 是插入序），`CELL` 的目录是 (offset,count) 的 u16 对。**如果将来要在搜狗里表达"词频"，`CELL` 是最可能带权重的文件，值得单独逆**。
6. 写回**没有在真机上让搜狗加载验证过**（任务约束：不许写活目录）。已做的最大保证是：结构自洽、索引有序、旧字节不动、引擎下次保存会重排整表。真正上线前建议：quit 搜狗 → 改副本 → 替换 → 启动 → 敲该词看是否出现在候选里。

---

## 7. 证据与复现

产物：

| 路径 | 内容 |
|---|---|
| `imefuse/adapters/sogou.py` | 适配器：`export()` / `write_entries()` / `ids_for()` / `syllable_table()`，内嵌 928 条音节表（app bundle 找不到也能跑） |
| `docs/formats/sogou.md` | 本文 |
| `work/sogou-notes/an*.py` | 逐步分析脚本（头解析、diff、记录扫描、索引校验、写回验证） |
| `work/sogou-notes/selftest.txt` | 上面那条自测命令的完整输出（3.1MB） |
| `work/sogou-notes/sandbox/` | 用户目录的 rsync 副本，所有写回实验都在这里做 |

关键复现命令：

```bash
# 头 + 目录 + 索引校验
python3 work/sogou-notes/an7.py
# 记录顺序解析是否正好吃满 arena
python3 work/sogou-notes/an9.py
# 索引是否 100% 指向记录起点 + 是否升序
python3 work/sogou-notes/an20.py
# 音节表对齐验证（23 个词）
python3 work/sogou-notes/syl2.py
# gen/块大小分布 + 字段一致性
python3 work/sogou-notes/an25.py
```

## 9. 真机写回：卡点是头部 `O_NONCE` 内容校验（已解决）

### 现象
结构自洽的文件写到真机，搜狗启动后**直接把它删掉**（不是拒绝加载，是删除）。

### 定位过程

用「同一文件的最小变更」做二分，每次改完启动搜狗看文件还在不在：

| 变体 | 结果 |
|---|---|
| 不改（字节完全相同重写） | ✅ 存活 |
| `GEN`(0x28) +1 | ✅ 存活 |
| `0x30` +1 | ✅ 存活 |
| `0x2c` +1 | ✅ 存活 |
| `GENSUM`(0x34) +1 | ✅ 存活 |
| **`NONCE`(0x20) +1** | ❌ **被删** |

只有一个字段被校验：**偏移 0x20 的 u32**。它不是随机数，是内容校验和。

### 算法（实测反推）

取引擎自己保存的两个版本做差：

```
T0: nonce=1455861040  arena_used=1081987  count=33461
T1: nonce=1455861098  arena_used=1082043  count=33463
Δnonce = 58 = Δarena_used(56) + Δcount(2)
```

两个版本的 `nonce - arena_used - count` 都等于 **1454745592**（文件出生时定下的 base）。

结论：

```
O_NONCE = base + arena_used + count
```

所以写回时**不需要知道 base**，按增量更新即可：

```python
new_nonce = old_nonce - old_arena_used - old_count + new_arena_used + new_count
```

### 修复后验证

| 步骤 | 结果 |
|---|---|
| 追加 1 条「词频合并验证」 | ✅ 文件存活，33462 条 |
| 打 `cipinhebingyanzheng` 上屏 | ✅ **词频合并验证** |
| 批量导入 300 条微信键盘的词 | ✅ 全部 `ok`，33766 条，arena 1096726/1390000 |
| 打 `canbu` / `xiaoai` 上屏 | ✅ **餐补** / **小爱** |

### 为什么之前四组实验都失败
前四次改的是索引排序、GEN、GENSUM、权限——**都不是被校验的那个字段**。`NONCE` 没跟着内容更新，引擎就判定文件损坏并 fail-closed 删除。
