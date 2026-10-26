<div align="center">

<img src="docs/assets/icon-preview.png" width="128" alt="imefuse">

# imefuse

**Your words should follow you, not your input method.**

Move the personal word-frequency data out of WeType / Sogou / Doubao on macOS —
and merge it into another IME without ever losing a word.

![Platform](https://img.shields.io/badge/platform-macOS-black)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Tests](https://img.shields.io/badge/tests-46%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

[English](#english) · [中文](#中文)

</div>

---

# English

## Why this exists

Every Chinese input method on macOS quietly keeps a private record of the words **you** use — how often, in what context, which candidate you picked over the one next to it. Stack up a few years of that and it becomes a personal language model: your slang, your colleagues' names, the typos you keep making and the phrases you always fix.

Then you switch IMEs, and it's gone. The formats are undocumented, mutually incompatible, and no vendor offers an import path. A decade of muscle memory resets to factory defaults because the data is locked in a file nobody is supposed to read.

**This project exists because that data is yours.**

We reversed the three formats byte by byte — the LevelDB with its `+6` byte shift, the `SGPU` container with its hidden content checksum, the encrypted UTF-16 records — and instead of keeping the notes to ourselves, we are publishing the parsers, the writers, the merge semantics and the tool.

If the vendors eventually ship a real import/export, this project becomes obsolete. That would be a good outcome. Until then, at least the data can move.

## What it does

| | WeType 微信键盘 | Sogou 搜狗 | Doubao 豆包 |
|---|---|---|---|
| Read the private dictionary | ✅ | ✅ | ✅ |
| Merge a dictionary back in | ✅ | ✅ | ✅ |
| Keeps a real hit count | ✅ | ✗ (save generation only) | ✅ |
| Verified end to end | ✅ | ✅ | ✅ |

"Verified" means: the word was written into the store, the IME was restarted, and typing its pinyin actually committed that word — not merely "the bytes are on disk". The full matrix and the raw evidence live in [docs/verification.md](docs/verification.md).

## Design principles

**Merge, never overwrite.** Importing can add a word, and it can raise a frequency. It can never delete an entry, never lower a count, never rewrite a record it did not have to touch. Re-importing the same file is a no-op.

**Refuse rather than corrupt.** Every adapter carries a format fingerprint. If an IME update changes the bytes, imefuse says *unknown format* and stays out of the way instead of writing garbage into your dictionary.

**Assume the application is running.** Every write stops the IME first, snapshots the whole data directory, writes, restarts, re-reads to confirm, and rolls back automatically on any failure.

**Own your data.** No network, no telemetry, no upload. The dictionary never leaves the machine unless you carry the file out yourself.

## Install

```bash
git clone https://github.com/<you>/imefuse.git
cd imefuse

python3 -m imefuse.cli detect     # what is installed here, and can we read it?
make app && open build/imefuse.app
```

Requires macOS 11+ and Python 3.9+. **No third-party dependencies** — the GUI is served by the standard library on `127.0.0.1`.

## Usage

```bash
imefuse detect                      # per-IME status and format fingerprint
imefuse gui                         # native window (or a browser tab)
imefuse export wetype               # -> exports/wetype-<ts>.jsonl + .tsv
imefuse import sogou FILE           # dry run: shows the merge diff
imefuse import sogou FILE --apply   # write (backup + quit IME + restart)
```

In the window: pick a source and a target, **预览** the diff, then **确认迁移**.

Moving between two Macs: **导出** gives you a download button, **合并导入** accepts a dropped or chosen `.jsonl` / `.tsv`. Uploads are validated before they are accepted.

## How it works

```
IME store  ←→  adapter (per IME)  ←→  canonical  ←→  merge engine  ←→  GUI / CLI
```

| Module | Purpose |
|---|---|
| `imefuse/discover.py` | finds installed IMEs, resolves data dirs, validates formats |
| `imefuse/leveldb/` | pure-python LevelDB (snappy, SSTable, WAL, crc32c) |
| `imefuse/canonical.py` | portable JSONL/TSV intermediate format |
| `imefuse/merge.py` | P1–P4 strategies, diff, never-lower guarantee |
| `imefuse/adapters/` | one adapter per input method |
| `imefuse/gui/` | stdlib HTTP server + static UI |
| `tools/AppMain.swift` | native window shell (WKWebView) |

The merge engine rescales counts onto the target's own distribution before combining them — a WeType weight of 300 and a Doubao frequency of 3 are not the same unit, and adding them raw would let one side swamp the other.

Format notes, including the two things that took the longest to crack, are in [docs/formats/](docs/formats/).

## Building

| Command | Result |
|---|---|
| `make app` | `build/imefuse.app` — native window, uses the system `python3` |
| `make dist` | single-file binary via PyInstaller (bundles a runtime) |
| `make test` | 46 unit tests, no real IME data required |
| `make detect` | what this machine has |

## Acknowledgements

This project would not exist without **[linux.do](https://linux.do)**.

It started as a throwaway question in that community — *"is there any way to move my Sogou words to WeType?"* — and turned into a shared reverse-engineering effort. The format guesses, the byte-level arguments, the person who insisted the `O_NONCE` field had to be a checksum when everyone else had given up on it, the testers who installed three IMEs on their own machines and typed nonsense into them for an evening: that is where this came from.

Thanks also to the wider reverse-engineering community whose notes on LevelDB, snappy and `crc32c` made the plumbing possible.

If you find this useful, the best thing you can do is file what you learn about a version we have not seen yet.

## License

MIT — see [LICENSE](LICENSE).

---

# 中文

## 为什么开源

每一款 macOS 中文输入法，都在私下记录**你自己**的用词：哪个词用得多、什么上下文、同样的拼音你为什么选了第二个候选而不是第一个。攒上几年，那就是一个只属于你的语言模型——你的口头禅、同事的名字、你反复打错又反复改回来的那些词。

然后你换了个输入法，清零。格式不公开、彼此不兼容、厂商也不提供导入通道。十年的肌肉记忆被重置成出厂默认，只因为数据锁在一个「本来就不该被读」的文件里。

**这个项目存在，是因为这些数据本来就该属于你。**

我们把三种格式逐字节逆了出来——LevelDB 里的 `+6` 位移、`SGPU` 容器里藏着的那个内容校验和、加密的 UTF-16 记录——然后没有把笔记留在自己手里，而是把解析器、写入器、合并语义和工具一起开源。

如果哪天厂商真的做了正经的导入导出，这个项目就该退休了。那是好事。在那之前，至少数据能跟着人走。

## 它做什么

| | 微信键盘 | 搜狗 | 豆包 |
|---|---|---|---|
| 读取私有词库 | ✅ | ✅ | ✅ |
| 合并写回 | ✅ | ✅ | ✅ |
| 有真实词频计数 | ✅ | ✗（只有保存代次） | ✅ |
| 行为级验证 | ✅ | ✅ | ✅ |

「行为级验证」指的是：写进词库 → 重启输入法 → 真的打拼音上屏那个词，而不是「字节写到磁盘上了」。完整验证矩阵和原始证据在 [docs/verification.md](docs/verification.md)。

## 设计原则

**只合并不覆盖。** 导入可以新增词条，可以抬高词频；但绝不删除已有条目、绝不降低计数、绝不重写不需要动的记录。同一份文件重复导入是空操作。

**宁可拒绝，也不写坏。** 每个适配器都带格式指纹。输入法一升级、字节对不上，imefuse 就报「格式不识别」然后收手，而不是往你的词库里塞垃圾。

**假设输入法正在运行。** 每次写入都先退出输入法、整目录快照、写入、重启、回读校验，任何一步失败自动回滚。

**数据属于你。** 不联网、不上报、不上传。词库不会离开这台机器，除非你自己把文件带走。

## 安装

```bash
git clone https://github.com/<you>/imefuse.git
cd imefuse

python3 -m imefuse.cli detect     # 先看这台机器上有什么
make app && open build/imefuse.app
```

要求 macOS 11+ 与 Python 3.9+。**零第三方依赖**——界面由标准库跑在 `127.0.0.1`。

## 使用

```bash
imefuse detect                      # 各输入法状态与格式指纹
imefuse gui                         # 原生窗口（或浏览器标签）
imefuse export wetype               # → exports/wetype-<ts>.jsonl + .tsv
imefuse import sogou FILE           # 只看 diff，不写
imefuse import sogou FILE --apply   # 真写（自动备份 + 退输入法 + 重启）
```

窗口里：选源和目标 → **预览** diff → **确认迁移**。

跨机器：**导出**给下载按钮，**合并导入**接受拖拽或选择的 `.jsonl` / `.tsv`，上传会先校验再收。

## 实现

```
输入法存储  ←→  adapter（每家具名）  ←→  canonical  ←→  merge 引擎  ←→  GUI / CLI
```

| 模块 | 作用 |
|---|---|
| `imefuse/discover.py` | 发现已装输入法、定位数据目录、校验格式 |
| `imefuse/leveldb/` | 纯 Python LevelDB（snappy / SSTable / WAL / crc32c） |
| `imefuse/canonical.py` | 可移植的 JSONL/TSV 中间格式 |
| `imefuse/merge.py` | P1–P4 合并策略、diff、「只升不降」保证 |
| `imefuse/adapters/` | 每家一个适配器 |
| `imefuse/gui/` | 标准库 HTTP 服务 + 静态界面 |
| `tools/AppMain.swift` | 原生窗口外壳（WKWebView） |

合并引擎会先把两边的计数按中位数换算到目标输入法的量纲再相加——微信键盘的权重 300 和豆包的频次 3 不是同一个单位，直接相加会让一边淹没另一边。

格式细节（包括花时间最久的两处）在 [docs/formats/](docs/formats/)。

## 构建

| 命令 | 产物 |
|---|---|
| `make app` | `build/imefuse.app`，原生窗口，使用系统 `python3` |
| `make dist` | PyInstaller 单文件（内置运行时） |
| `make test` | 46 个单元测试，不需要真机数据 |
| `make detect` | 看这台机器有什么 |

## 致谢

没有 **[linux.do](https://linux.do)** 就没有这个项目。

它起源于那个社区里一句随口的提问——「有没有办法把搜狗的词搬到微信键盘」——然后变成了一次集体逆向。格式的猜测、字节级的争论、在所有人都放弃之后仍然坚持「`O_NONCE` 一定是个校验和」的那位、以及在自己机器上装三个输入法敲一晚上乱码的测试者们——这东西是从那里长出来的。

同样感谢更广义的逆向社区：关于 LevelDB、snappy 和 `crc32c` 的那些笔记，让底层的管道成为可能。

如果你觉得它有用，最好的回报是把你在**我们还没见过的版本**上发现的东西写下来。

## 许可证

MIT，见 [LICENSE](LICENSE)。
