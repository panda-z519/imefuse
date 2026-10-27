<div align="center">

<img src="docs/assets/icon-preview.png" width="128" alt="imefuse">

# imefuse

**你的词，应该跟着你走，而不是跟着输入法。**

把 macOS 上微信键盘 / 搜狗 / 豆包里的个人词频拿出来，
合并进另一个输入法——一个词都不丢。

*Your words should follow you, not your input method.
Move the personal word-frequency data out of WeType / Sogou / Doubao on macOS and merge it into another IME without ever losing a word.*

![Platform](https://img.shields.io/badge/platform-macOS-black)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Tests](https://img.shields.io/badge/tests-98%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

[中文](#中文) · [English](#english)

</div>

---

# 中文

## 起因

我换了输入法。用了几天，不对劲——**高频词全没了**。

不是"少了一些"，是那些我打了五六年、闭着眼睛都能盲打的词，一个字都调不出来。以前敲两下就上屏的名字、口头禅、常用短语，现在要在一堆系统词里翻。输入法好像突然不认识我了。

我去翻了翻，才发现原因：每家输入法都把「你怎么打字」记在一个私有文件里——哪个词用得多、同样的拼音你为什么选第二个候选、上一句后面习惯接什么。攒几年，那是一个只属于你的语言模型。但这个文件格式不公开、彼此不兼容、厂商也不提供导入导出。**你换输入法，就是从头开始当新人。**

后来在 [linux.do](https://linux.do) 刷到一个佬友发帖，问的正是同一件事：换了输入法，怎么把老词库搬过去。帖子里有人试过、有人放弃、有人猜格式。

我想，那就自己来吧。

于是把微信键盘、搜狗、豆包三家的格式逐字节逆了出来——LevelDB 里的 `+6` 位移、`SGPU` 容器里那个藏起来的内容校验和、整体加密的 UTF-16 记录——然后把解析器、写入器、合并语义和工具全部开源。

**这些数据本来就该属于你。** 你打的字，凭什么是输入法厂商的资产。

如果哪天厂商真的做了正经的导入导出，这个项目就该退休了。那是好事。在那之前，至少你的词能跟着你走。

## 它做什么

| | 微信键盘 | 搜狗 | 豆包 |
|---|---|---|---|
| 读取私有词库 | ✅ | ✅ | ✅ |
| 写入新词条 | ✅ | ✅ | ✅ |
| 抬高已有词的词频 | ✅ | ✗（只有保存代次） | ✗（写回是仅加词） |
| 行为级验证 | ✅ | ✅ | ✅ |

「行为级验证」指的是：写进词库 → 重启输入法 → 真的打拼音上屏那个词，而不是
「字节写到磁盘上了」。完整验证矩阵和原始证据在 [docs/verification.md](docs/verification.md)。

各家还有各自的上限，`./imefuse.sh doctor` 会逐条列出来，见下方[能力限制](#能力限制)。

## 设计原则

**只合并不覆盖。** 导入可以新增词条，可以抬高词频；但绝不删除已有条目、绝不降低计数、绝不重写不需要动的记录。同一份文件重复导入是空操作。

**宁可拒绝，也不写坏。** 每个适配器都带格式指纹。输入法一升级、字节对不上，imefuse 就报「格式不识别」然后收手，而不是往你的词库里塞垃圾。

**失败就停，不会继续往下写。** 每次写入都先整目录快照、退出输入法、写入、重启；
任何一步抛错都会把输入法重新拉起来，快照留在 `backups/` 里供你手动回滚。

**数据属于你。** 不联网、不上报、不上传。词库不会离开这台机器，除非你自己把文件带走。

## 安装

只依赖系统自带的 `bash` 与 `python3`（3.9+），没有 pip 依赖，也不编译任何东西。

```bash
git clone https://github.com/panda-z519/imefuse.git
cd imefuse

./imefuse.sh doctor        # 先看这台机器能不能用、每家的硬限制
./imefuse.sh detect        # 装了哪些输入法
./imefuse.sh gui           # 起界面（默认 127.0.0.1:8770，未做可用性验证）
```

`doctor` 会明确告诉你哪些是「就绪」、哪些「可用·受限」、哪些「不可用」，以及原因。

> 系统自带的 `/usr/bin/python3` 是个占位符：没装 Command Line Tools 时它一启动就退出。
> `imefuse.sh` 会真跑一次来挑解释器，找不到时直接告诉你去 `xcode-select --install`，
> 而不是起一半再报错。也可以用 `--python /path/to/python3` 手动指定。

## 使用

### 第一次用：先跑 doctor

```bash
./imefuse.sh doctor
```

它会把三家的状态摊开，告诉你**能不能用、为什么不能、用了有什么代价**：

```
imefuse 0.1.0 · Python 3.9+ · /path/to/imefuse

wetype   就绪         微信键盘 WeType
         · 无已知限制

sogou    可用·受限      搜狗输入法
         ! 词库没有词频计数器，合并只能加词条

doubao   可用·受限      豆包输入法
         ·  还能再写入约 1061 条（定长容器 2500 条 / 80000 字节）
         !  写回是只加词：已存在的词不会抬高词频

可迁移: wetype, sogou, doubao
```

三个档位：

| 档位 | 含义 | 你能做什么 |
|---|---|---|
| 就绪 | 读、写、合并全部正常 | 随便用 |
| 可用·受限 | 能用，但有明确上限 | 看下面那几条限制再决定 |
| 不可用 | 读不了或写不了 | 选不中，会告诉你原因 |

加 `-v` 会展开每条限制的技术原因（例如搜狗那条会补上「写回需同步更新头部
`O_NONCE` 内容校验，否则引擎会判定文件损坏并删除」）。

### 日常用法：一条命令进向导

```bash
./imefuse.sh
```

```
── imefuse ────────────────────────────────────────────────────────
  词库在本机，不联网、不上传。写入前会先备份并退出输入法。

  要做什么？（q 退出）
     1) 查看状态（每家能不能用、有多少词、有哪些硬限制）
     2) 导出（把某家的个人词导出成 .jsonl / .tsv）
     3) 一键迁移（从一家导出并合并进另一家（先看 diff 再写））
     4) 合并导入（把 exports/ 里的一份文件合并进某家）
     5) 备份（整目录快照某家的词库）
     6) 回滚（把某家恢复到某个备份）
     7) 限制说明（逐条列出每家的能力档与原因）
   q) 退出
  选择编号 [1]:
```

选完动作接着问对象。**每一步都能 `q` 退出，写入前一定先给你看 diff**：

```
  合并进哪个输入法？
     1) 微信键盘 WeType（就绪 · 20248 条）
     2) 搜狗输入法（可用·受限 · 33772 条）
     3) 豆包输入法（可用·受限 · 1067 条 · 还能写约 1061 条）
   q) 退出
  选择编号 [1]: 3

  新增 14418 · 累加 0 · 同词异音 11 · 不变 33772 · 删除 0 · 降频 0
    需要     xu yao     bump   旧 1     新 1009  → 1010
    以下     yi xia     add    旧 0     新 939   → 939
    …
  确认写入 豆包输入法？会先备份、退出输入法、写完重启。 (y/N)
```

按 `n` 就是**什么都不写**。按 `y` 之后你能看到真实的 8 个阶段：

```
[1/8] ✓ 读取源词库 微信键盘 WeType · 20248 条  8.9s
[2/8] ✓ 检查目标输入法 · 豆包输入法  0.0s
[3/8] ✓ 读取目标现有词库 · 1067 条  0.1s
[4/8] ✓ 计算合并计划 · 新增 14418 · 累加 0  0.6s
[5/8] ✓ 备份整个词库目录 · 1.1M  0.4s
[6/8] ✓ 退出输入法 · 已退出  1.2s
[7/8] ✓ 写入 14418 条  0.3s
[8/8] ✓ 重启输入法  2.0s
```

### 三种典型场景

**A. 换了输入法，想把老词搬过去**（最常见）

```bash
./imefuse.sh          # 选 3 一键迁移 → 老输入法 → 新输入法 → 看 diff → y
```

**B. 两台 Mac 之间搬**

旧机器上 `./imefuse.sh` 选 `2 导出`，把 `exports/wetype-20261027-110000.jsonl`
拷到新机器，放进新机器的 `exports/` 目录，然后选 `4 合并导入`。也可以用 GUI
的上传按钮（接受拖拽，会先校验再收）。

**C. 只是想先看看、留个底**

```bash
./imefuse.sh          # 选 2 导出（只读，不改任何数据）
                      # 或选 5 备份（整目录快照）
```

### 单步命令（脚本化 / crontab）

不想走交互时，每个动作都有对应子命令：

```bash
./imefuse.sh doctor                          # 能力分级 + 已知限制（-v 看细节）
./imefuse.sh detect                          # 各输入法状态与格式指纹（--json）
./imefuse.sh status                          # 机器可读的完整状态
./imefuse.sh export wetype                   # → exports/wetype-<ts>.jsonl + .tsv
./imefuse.sh import doubao FILE              # 只看 diff，不写
./imefuse.sh import doubao FILE --apply      # 真写
./imefuse.sh gui                             # 浏览器界面
```

通用选项写在命令**前面**：

| 选项 | 作用 |
|---|---|
| `--port <n>` | GUI 端口，默认 8770；被占用时自动往后找 |
| `--data <目录>` | 导出 / 备份 / 状态放在哪 |
| `--home <目录>` | 把 `$HOME` 指到别处，用于在仿真数据上试跑 |
| `--python <路径>` | 指定解释器，默认自动找 ≥3.9 的那个 |
| `--no-browser` | GUI 起来后不自动打开浏览器 |

`./imefuse.sh --help` 是完整清单；`make doctor / detect / run / test` 是简写。

### GUI（未做真实测试，可用性未验证）

```bash
./imefuse.sh gui
```

界面里：选源和目标 → **预览** diff → **确认迁移**。耗时操作都有进度条；
首屏探测三家词库要 5–10 秒（微键 20 万条键值），这段时间也有进度。

> **这个界面的可用性还没有验证过。** 后端（解析、合并、写入、备份）做过行为级
> 验证，向导和 CLI 也跑过真实迁移；但浏览器这一层没做真实测试 ——
> `tests/` 里没有 GUI 或 HTTP 接口的用例，也没有前端测试，我只在开发时手点过。
> 布局、异常提示、上传下载、回滚这些交互都可能有没暴露出来的问题。
>
> **想稳妥就用命令行**：`./imefuse.sh`（向导）或单步子命令，它们走的是同一份
> `imefuse/actions.py`，但路径更短、更容易验证。

> 界面是本地浏览器页面 + 本地 Python 服务，走 `127.0.0.1` 回环，不上外网。
> 词库不会离开这台机器。

### 数据都在哪

| 位置 | 内容 |
|---|---|
| `exports/` | 导出文件（`.jsonl` 机器读，`.tsv` 人读） |
| `backups/` | 每次写入前自动做的整目录快照 |
| `state/imported.json` | 导入历史（哪份文件、什么时候、写了多少条） |

用 `--data` 可以整体搬走。这些目录里的东西**都是你的隐私，别提交到仓库**
（`.gitignore` 已经排除）。

### 出问题了

1. 先跑 `./imefuse.sh doctor -v`，看是不是被判定为不可用
2. 写入失败或效果不对 → `./imefuse.sh` 选 `6 回滚`，挑写入前那个 `pre-import` 快照
3. 想确认不会写坏 → `import` 不加 `--apply`，它只算 diff，一个字节都不动

## 实现

```
输入法存储  ←→  adapter（每家具名）  ←→  canonical  ←→  merge 引擎  ←→  GUI / CLI
```

| 模块 | 作用 |
|---|---|
| `imefuse.sh` | 唯一入口：选项解析、挑解释器、分发到 CLI |
| `imefuse/wizard.py` | 交互向导：选动作 → 选对象 → 确认后执行 |
| `imefuse/actions.py` | 动作层：导出 / diff / 写入 / 迁移 / 备份，CLI 与 GUI 共用 |
| `imefuse/progress.py` | 进度显示：有真实阶段才给 n/N，否则只给耗时 |
| `imefuse/limits.py` | 能力分级（就绪 / 可用·受限 / 不可用）与每家的硬限制 |
| `imefuse/discover.py` | 发现已装输入法、定位数据目录、校验格式 |
| `imefuse/leveldb/` | 纯 Python LevelDB（snappy / SSTable / WAL / crc32c） |
| `imefuse/canonical.py` | 可移植的 JSONL/TSV 中间格式 |
| `imefuse/merge.py` | P1–P4 合并策略、diff、「只升不降」保证 |
| `imefuse/adapters/` | 每家一个适配器 |
| `imefuse/gui/` | 标准库 HTTP 服务 + 静态界面 |

合并引擎会先把两边的计数按中位数换算到目标输入法的量纲再相加——微信键盘的权重 300 和豆包的频次 3 不是同一个单位，直接相加会让一边淹没另一边。

格式细节（包括花时间最久的两处）在 [docs/formats/](docs/formats/)。

## 能力限制

有些限制不是 bug，是词库格式本身决定的，`doctor` 会逐条列出来：

| 输入法 | 限制 | 后果 |
|---|---|---|
| 豆包 | `usr.dat` 是定长容器（2500 条 / 80000 字节，不增长） | 快满时只能再写几百条；满了要清理词库 |
| 豆包 | 写回按词面去重后跳过已有词 | **只能加新词，已存在的词不会抬高词频** |
| 豆包 | 多账号且读不到登录 uid 时按 mtime 选目录 | 会写进最近修改的那个账号 |
| 搜狗 | 词库里没有计数器，`gen` 是保存代次 | 只能加词条，无法迁移词频 |
| 微信键盘 | 词库目录带版本号（`userDict/v5`） | 厂商换版本后报「格式不识别」，而不是静默乱读 |

## 开发

| 命令 | 作用 |
|---|---|
| `make test` | 98 个单元测试，不需要真机数据 |
| `make lint` | byte-compile + `ruff`（装了才跑） |
| `make doctor` / `detect` / `run` | 同 `./imefuse.sh` 的对应子命令 |

仓库就是交付物：一个 `imefuse.sh` 加纯标准库的 Python，没有编译步骤，
也没有打包产物。

**测试覆盖到哪里**：适配器（三家格式的解析 / 序列化 / 写回）、合并语义、
LevelDB、canonical、能力分级、进度、入口脚本的参数契约、向导的决策流程。
**没有覆盖**：GUI 与 HTTP 接口（`imefuse/gui/` 没有任何用例）、真实浏览器里
的前端交互、以及各家的行为级验证（那部分靠手工跑，结果在
[docs/verification.md](docs/verification.md)）。

## 致谢

这个项目应该归给 **[linux.do](https://linux.do)**。

起因是我自己换了输入法，高频词全丢了。真正让我动手的，是在 L 站刷到那位佬友的帖子——
问的正是「换了输入法，怎么把老词库搬过去」。那条帖子下面有人试过、有人放弃、有人猜格式。
如果没有看到它，我大概只是抱怨两句就忍了。

之后的每一段推进都有那个社区的影子：

- **格式的猜测**——`!v2u!` 的值为什么要整体 `+6` 位移、`SGPU` 里那个字段到底是什么，
  都是在帖子里一条条争出来的
- **所有人都放弃之后**，仍然坚持「`O_NONCE` 一定是个校验和」的那位——最后正是这条
  让搜狗的写回从「文件被引擎删掉」变成「能存活」
- 在自己机器上装了三个输入法、敲一晚上乱码、只为了确认「写进去的那个词真的会上屏」的测试者们

「行为级验证」这个标准也是那里来的：不是「字节写到磁盘上了」，而是重启输入法之后
打拼音真的能上屏。完整验证矩阵和原始证据在 [docs/verification.md](docs/verification.md)。

同样感谢更广义的逆向社区：关于 LevelDB、snappy 和 `crc32c` 的那些公开笔记，
让底层的管道成为可能。

如果你觉得它有用，最好的回报是把你在**我们还没见过的版本**上发现的东西写回来——
格式变了、字段对不上、或者某家又出了新的输入法，开个 issue 或在 L 站说一声都行。

## 许可证

MIT，见 [LICENSE](LICENSE)。

---

# English

## Why this exists

I switched input methods. A few days later something felt off: **every high-frequency word was gone.**

Not "a few missing" — the names I had typed for five or six years without thinking, the phrases my fingers knew by heart, none of them came up. What used to take two keystrokes now meant scrolling through a list of system defaults. The IME no longer knew me.

Digging in, I found out why: every Chinese IME keeps a private file recording *how you type* — which words you use most, which candidate you pick for the same pinyin, what tends to follow what. After a few years that is a personal language model. But the format is undocumented, incompatible with everyone else's, and no vendor offers import/export. **Switching IMEs means starting over as a beginner.**

Then I saw a post on [linux.do](https://linux.do) from someone asking exactly the same thing: they had changed IMEs, how do they move the old dictionary across? In that thread people had tried, people had given up, people were guessing at the format.

I figured I'd just do it myself.

So I reversed all three formats byte by byte — the `+6` byte shift inside LevelDB, the hidden content checksum in the `SGPU` container, the fully encrypted UTF-16 records — and published the parsers, the writers, the merge semantics and the tool.

**This data is yours.** You typed those words; why would they be a vendor's asset?

If the vendors eventually ship a real import/export, this project becomes obsolete. That would be a good outcome. Until then, at least your words can move with you.

## What it does

| | WeType 微信键盘 | Sogou 搜狗 | Doubao 豆包 |
|---|---|---|---|
| Read the private dictionary | ✅ | ✅ | ✅ |
| Add new entries | ✅ | ✅ | ✅ |
| Raise an existing word's frequency | ✅ | ✗ (save generation only) | ✗ (write-back is add-only) |
| Verified end to end | ✅ | ✅ | ✅ |

"Verified" means: the word was written into the store, the IME was restarted, and typing
its pinyin actually committed that word — not merely "the bytes are on disk". The full
matrix and the raw evidence live in [docs/verification.md](docs/verification.md).

Each IME has its own ceiling; `./imefuse.sh doctor` lists them, see
[Known limits](#known-limits) below.

## Design principles

**Merge, never overwrite.** Importing can add a word, and it can raise a frequency. It can never delete an entry, never lower a count, never rewrite a record it did not have to touch. Re-importing the same file is a no-op.

**Refuse rather than corrupt.** Every adapter carries a format fingerprint. If an IME update changes the bytes, imefuse says *unknown format* and stays out of the way instead of writing garbage into your dictionary.

**Stop on failure, never keep writing.** Every write snapshots the whole data directory,
quits the IME, writes, then restarts it; any error along the way still brings the IME
back up, and the snapshot stays in `backups/` for you to roll back by hand.

**Own your data.** No network, no telemetry, no upload. The dictionary never leaves the machine unless you carry the file out yourself.

## Install

Only needs the `bash` and `python3` (3.9+) that ship with macOS. No pip dependencies, nothing to compile.

```bash
git clone https://github.com/panda-z519/imefuse.git
cd imefuse

./imefuse.sh doctor        # can this machine do it, and what are the hard limits
./imefuse.sh detect        # which IMEs are installed
./imefuse.sh gui           # serve the UI on 127.0.0.1:8770 (usability not verified)
```

`doctor` says which IMEs are `ready`, `limited` or `blocked`, and why.

> `/usr/bin/python3` on macOS is a stub: without Command Line Tools it exits immediately.
> `imefuse.sh` actually runs a candidate before choosing it, and if none works it tells you to
> run `xcode-select --install` instead of failing halfway. `--python /path/to/python3` overrides.

## Usage

### First run: `doctor`

```bash
./imefuse.sh doctor
```

It lays out all three IMEs and tells you **whether it works, why not, and what it costs**:

```
imefuse 0.1.0 · Python 3.9+ · /path/to/imefuse

wetype   ready          WeType 微信键盘
         · no known limits

sogou    limited        Sogou 搜狗
         !  no frequency counter in the dictionary — merge can only add entries

doubao   limited        Doubao 豆包
         ·  about 1061 more entries fit (fixed container: 2500 records / 80000 bytes)
         !  write-back is add-only: an existing word's frequency is never raised

usable: wetype, sogou, doubao
```

Three levels:

| Level | Meaning | What you can do |
|---|---|---|
| ready | read, write and merge all work | anything |
| limited | works, with a known ceiling | read the listed limits first |
| blocked | cannot read or cannot write | not selectable; the reason is shown |

`-v` expands the technical reason behind each limit.

### Day to day: one command, then the wizard

```bash
./imefuse.sh
```

```
── imefuse ────────────────────────────────────────────────────────
  Your dictionary stays on this machine. Every write is backed up first,
  and the IME is quit before the file is touched.

  What do you want to do? (q to quit)
     1) Status      — what works, how many words, which hard limits
     2) Export      — one IME to .jsonl / .tsv
     3) Migrate     — export one, merge into another (diff first)
     4) Import      — merge a file from exports/ into one IME
     5) Backup      — snapshot one IME's dictionary directory
     6) Restore     — roll one IME back to a snapshot
     7) Limits      — every capability level and its reason
   q) quit
```

It then asks which IME / which file. **`q` leaves at any step, and you always see the
diff before anything is written**:

```
  Merge into which IME?
     1) WeType 微信键盘 (ready · 20248 words)
     2) Sogou 搜狗 (limited · 33772 words)
     3) Doubao 豆包 (limited · 1067 words · about 1061 more fit)
   q) quit
  Choose a number [1]: 3

  add 14418 · bump 0 · conflict 11 · keep 33772 · deleted 0 · lowered 0
    需要   xu yao   bump   old 1   new 1009  → 1010
    以下   yi xia   add    old 0   new 939   → 939
    …
  Write into Doubao 豆包? It will back up, quit the IME, write, then restart. (y/N)
```

`n` writes **nothing at all**. `y` gives you the real 8 stages:

```
[1/8] ✓ read source dictionary · WeType 微信键盘 · 20248 words  8.9s
[2/8] ✓ check target IME · Doubao 豆包  0.0s
[3/8] ✓ read target dictionary · 1067 words  0.1s
[4/8] ✓ plan merge · add 14418 · bump 0  0.6s
[5/8] ✓ snapshot the whole directory · 1.1M  0.4s
[6/8] ✓ quit the IME · quit  1.2s
[7/8] ✓ write 14418 entries  0.3s
[8/8] ✓ restart the IME  2.0s
```

### Three typical scenarios

**A. You switched IMEs and want the old words back** (the common one)

```bash
./imefuse.sh          # 3 Migrate → old IME → new IME → read the diff → y
```

**B. Moving between two Macs**

On the old machine pick `2 Export`, copy `exports/wetype-20261027-110000.jsonl` across,
drop it into `exports/` on the new machine, then pick `4 Import`. The GUI also accepts a
dropped file and validates it before accepting.

**C. Just looking, or making a safety copy**

```bash
./imefuse.sh          # 2 Export (read-only, touches nothing)
                      # or 5 Backup (whole-directory snapshot)
```

### Single steps, for scripting

```bash
./imefuse.sh doctor                          # capability levels + known limits (-v for detail)
./imefuse.sh detect                          # per-IME status and format fingerprint (--json)
./imefuse.sh status                          # full machine-readable state
./imefuse.sh export wetype                   # -> exports/wetype-<ts>.jsonl + .tsv
./imefuse.sh import doubao FILE              # dry run: shows the merge diff, writes nothing
./imefuse.sh import doubao FILE --apply      # write (backup + quit IME + restart)
./imefuse.sh gui                             # browser UI
```

Global options go **before** the command:

| Option | Effect |
|---|---|
| `--port <n>` | GUI port, default 8770; walks forward if taken |
| `--data <dir>` | where exports / backups / state live |
| `--home <dir>` | point `$HOME` elsewhere, for dry runs against simulated data |
| `--python <path>` | pick the interpreter instead of auto-detecting ≥3.9 |
| `--no-browser` | don't open a browser after starting the GUI |

`./imefuse.sh --help` is the full list; `make doctor / detect / run / test` are shorthands.

> Without a terminal (a pipe, CI) the wizard refuses to start and exits 2 — it never
> treats "EOF on stdin" as "accept the default", because silently writing in an
> unattended run is the worst possible outcome.

### GUI (untested — usability not verified)

```bash
./imefuse.sh gui
```

Pick a source and a target, preview the diff, then confirm. Long operations have progress
bars; the first screen parses all three dictionaries (5–10 s on a 200k-key WeType store)
and shows progress while it does.

> **The interface has not been verified.** The backend (parsing, merging, writing,
> backups) has been behaviourally verified, and the wizard and CLI have run real
> migrations — but the browser layer has never been properly tested. `tests/` has no
> cases for the GUI or the HTTP endpoints, and no front-end tests; it has only been
> clicked through by hand during development. Layout, error messages, upload/download
> and restore flows may all have problems that never surfaced.
>
> **If you want the safer path, use the command line**: `./imefuse.sh` (the wizard) or
> the single-step subcommands. They call the same `imefuse/actions.py`, but the path is
> shorter and easier to verify.

> The UI is a local browser page talking to a local Python process over `127.0.0.1`.
> Nothing goes to the network, and no dictionary leaves this machine.

### Where the data lives

| Path | Contents |
|---|---|
| `exports/` | exported files (`.jsonl` for machines, `.tsv` for humans) |
| `backups/` | automatic whole-directory snapshots taken before each write |
| `state/imported.json` | import history: which file, when, how many entries |

`--data` moves all of it. Everything in those directories is **your private data — do not
commit it** (already in `.gitignore`).

### When something goes wrong

1. `./imefuse.sh doctor -v` first — you may simply be `blocked` for a clear reason
2. Write failed or the result looks wrong → `./imefuse.sh`, pick `6 Restore`, choose the
   `pre-import` snapshot taken just before the write
3. Want to be sure nothing gets written → `import` without `--apply`; it only computes
   the diff and touches no bytes

## How it works

```
IME store  ←→  adapter (per IME)  ←→  canonical  ←→  merge engine  ←→  GUI / CLI
```

| Module | Purpose |
|---|---|
| `imefuse.sh` | the only entry point: option parsing, interpreter selection, dispatch |
| `imefuse/wizard.py` | the interactive wizard: pick an action, pick the object, confirm |
| `imefuse/actions.py` | action layer: export / diff / write / migrate / backup, shared by CLI and GUI |
| `imefuse/progress.py` | progress display: n/N only for real stages, otherwise elapsed time |
| `imefuse/limits.py` | capability levels (ready / limited / blocked) and each IME's hard limits |
| `imefuse/discover.py` | finds installed IMEs, resolves data dirs, validates formats |
| `imefuse/leveldb/` | pure-python LevelDB (snappy, SSTable, WAL, crc32c) |
| `imefuse/canonical.py` | portable JSONL/TSV intermediate format |
| `imefuse/merge.py` | P1–P4 strategies, diff, never-lower guarantee |
| `imefuse/adapters/` | one adapter per input method |
| `imefuse/gui/` | stdlib HTTP server + static UI |

The merge engine rescales counts onto the target's own distribution before combining them — a WeType weight of 300 and a Doubao frequency of 3 are not the same unit, and adding them raw would let one side swamp the other.

Format notes, including the two things that took the longest to crack, are in [docs/formats/](docs/formats/).

## Known limits

Some of these are not bugs, they follow from the on-disk format. `doctor` spells them out:

| IME | Limit | Consequence |
|---|---|---|
| Doubao | `usr.dat` is a fixed-size container (2500 records / 80000 bytes, never grows) | only a few hundred more entries fit once it is nearly full |
| Doubao | write-back skips words that already exist | **new words only — an existing word's frequency is never raised** |
| Doubao | with several accounts and no readable login uid, the newest `usr.dat` wins | you may write into the wrong account |
| Sogou | no hit counter; `gen` is a save generation | words can be added, frequencies cannot be moved |
| WeType | the dict dir carries a version (`userDict/v5`) | a version bump reports "unknown format" instead of misreading it |

## Building

| Command | Result |
|---|---|
| `make test` | 98 unit tests, no real IME data required |
| `make lint` | byte-compile + `ruff` (when installed) |
| `make doctor` / `detect` / `run` | same as the matching `./imefuse.sh` subcommand |

The repository is the deliverable: one `imefuse.sh` plus pure-stdlib Python. No build
step, no artefact to package.

**What the tests cover**: the adapters (parsing, serialising and writing back all three
formats), the merge semantics, the LevelDB reader, canonical, capability levels,
progress, the entry script's argument contract, and the wizard's decision flow.
**What they do not cover**: the GUI and its HTTP endpoints (there is no test in
`imefuse/gui/`), front-end behaviour in a real browser, or the per-IME behavioural
verification — that was done by hand, and the record lives in
[docs/verification.md](docs/verification.md).

Copy it to any Mac with `python3` 3.9+ and it runs.

## Acknowledgements

This project belongs to **[linux.do](https://linux.do)**.

It started with me switching IMEs and losing every high-frequency word. What actually got me
building was a post on linux.do from someone asking the same question — *"I changed IMEs, how do
I move my old dictionary across?"* People in that thread had tried, had given up, had guessed at
the format. Without seeing it I would probably have grumbled and lived with it.

Every step after that had that community's fingerprints on it:

- **The format guesses** — why `!v2u!` values are shifted by `+6`, what a particular field in
  `SGPU` actually means — were argued out line by line in those threads.
- **The person who insisted `O_NONCE` had to be a checksum when everyone else had given up on it** —
  that is precisely what turned Sogou's write path from "the engine deletes the file" into
  "the engine keeps it".
- The testers who installed three IMEs on their own machines and typed nonsense into them for an
  evening, just to confirm the imported word really shows up as a candidate.

The "behavioural verification" standard came from there too: not "the bytes hit the disk" but
"after a restart, typing the pinyin actually produces the word". The full matrix and the raw
evidence are in [docs/verification.md](docs/verification.md).

Thanks also to the wider reverse-engineering community whose public notes on LevelDB, snappy and
`crc32c` made the plumbing possible.

If you find this useful, the best thing you can do is file what you learn about a version we have
not seen yet — a changed format, a field that no longer lines up, or a fourth IME worth supporting.
An issue here or a post on linux.do both work.

## License

MIT — see [LICENSE](LICENSE).
