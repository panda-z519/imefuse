# imefuse

**Migrate your personal word-frequency data between macOS Chinese input methods — merge, never overwrite.**

imefuse reads the private user dictionary of **WeType (微信键盘)**, **Sogou (搜狗)** and **Doubao (豆包)** on macOS, exports it to a portable format, and merges it into another IME — preserving existing entries, never lowering a frequency, never deleting a word.

> Personal dictionaries are extremely sensitive. Everything runs locally; nothing is uploaded. `exports/`, `backups/` and `runs/` contain your own typing data — never commit or share them.

---

## Status

| IME | Version tested | Export | Merge import | Behaviour verified |
|---|---|---|---|---|
| WeType 微信键盘 | 2.2.0 → 3.0.0 | ✅ | ✅ | ✅ frequency changes candidate order |
| Sogou 搜狗 | 6.25.3.12004 | ✅ | ✅ | ✅ imported words show up as candidates |
| Doubao 豆包 | 1.0.3 | ✅ | ✅ | ✅ imported words show up as candidates |

"Behaviour verified" means: the word was written into the store, the IME was restarted, and typing its pinyin actually committed that word — not just "the bytes are on disk".

See [docs/verification.md](docs/verification.md) for the full test matrix and evidence.

## Install

```bash
git clone https://github.com/your-name/imefuse.git
cd imefuse

# 1) run straight from the source tree (no dependencies, Python 3.9+)
python3 -m imefuse.cli detect

# 2) or install the `imefuse` command
python3 -m pip install -e .

# 3) or build a double-clickable app
make app && open build/imefuse.app
```

There are **zero third-party dependencies**. The GUI is served by Python's stdlib on `127.0.0.1`.

## Usage

```bash
imefuse detect                      # what is installed on this Mac, and can we read it?
imefuse gui                         # local web UI, opens in your browser
imefuse export wetype               # -> exports/wetype-<ts>.jsonl + .tsv
imefuse import sogou FILE           # dry run: shows the merge diff
imefuse import sogou FILE --apply   # really write (backup + quit IME + restart)
imefuse status                      # machine-readable summary
```

In the GUI: pick a source and a target, hit **预览** to see the diff (new / bumped / same word different pinyin / untouched), then **确认迁移**.

### Moving data between machines

- **导出** produces `exports/<ime>-<ts>.jsonl` (plus a `.tsv` mirror); the GUI gives you a download button for each.
- **合并导入** accepts a file from disk — click 选择文件 or drag a `.jsonl` / `.tsv` onto the drop zone. Uploads are validated before they are accepted: unknown extensions, empty files and rows without a word are rejected with a message.

So the cross-machine flow is: export on Mac A → carry the `.jsonl` over → upload it on Mac B → preview the diff → apply.

## Safety model

Every write follows the same sequence, and none of it is optional:

1. **Snapshot** the whole IME data directory to `backups/<ts>-<ime>-<label>/`
2. **Quit** the IME and wait for the process to actually disappear
3. **Merge** — existing keys are only ever raised, never removed, never rewritten
4. **Restart** the IME and re-read the store to confirm
5. On any failure, **restore** the snapshot automatically

Additional rules baked into the merge engine:

- `merged >= old` always; a strategy that would lower a count is rejected
- untouched entries are not rewritten
- re-importing the same export is a no-op (identical counts are `keep`)

**Cloud sync is the one thing we cannot control.** All three IMEs sync user dictionaries to an account. If you import while logged in, the cloud copy may overwrite your local merge. Sign out or go offline for the duration.

## How it works

```
IME store  <->  adapter (per IME)  <->  canonical  <->  merge engine  <->  GUI / CLI
```

| Module | Purpose |
|---|---|
| `imefuse/discover.py` | finds installed IMEs, resolves data dirs, validates on-disk formats |
| `imefuse/leveldb/` | pure-python LevelDB (snappy + SSTable + WAL + crc32c) |
| `imefuse/canonical.py` | portable JSONL/TSV format |
| `imefuse/merge.py` | P1–P4 merge strategies, diff, never-lower guarantee |
| `imefuse/adapters/*.py` | one adapter per IME |
| `imefuse/gui/` | stdlib HTTP server + static UI |

Format notes — including the two things that took the longest to crack (WeType's `+6` byte shift and Sogou's `O_NONCE` content checksum) — are in [docs/formats/](docs/formats/).

## Supported versions

imefuse refuses to write when the on-disk format does not match what it was reversed against:

| IME | Fingerprint |
|---|---|
| WeType | LevelDB + `!v2u!` records, structural check ≥ 95 % |
| Sogou | magic `SGPU`, container version `20160521`, sorted index |
| Doubao | `dict_id == 12001`, geometry `64 + 140 + 2500×4 + 80000 == size` |

A mismatch shows up as **unknown-format** and the IME is excluded from migration instead of being corrupted.

## Building

| Command | Result |
|---|---|
| `make app` | `build/imefuse.app` — thin bundle, uses the machine's `python3`, ad-hoc signed |
| `make dist` | single-file binary via PyInstaller (bundles a Python runtime) |
| `make test` | 46 unit tests, no real IME data required |
| `make lint` | byte-compile everything |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The most useful contributions are format notes for IME versions we have not seen, and adapters for other macOS Chinese IMEs.

## License

MIT — see [LICENSE](LICENSE).

---

# 中文说明

**把你在 macOS 中文输入法里的个人词频，迁移到另一个输入法——只合并，不覆盖。**

支持微信键盘 / 搜狗 / 豆包。读取各自的私有词库，导出成统一格式，再合并进目标输入法：**已有词条不删、词频不降、无关条目一个字节都不改**。

## 快速开始

```bash
python3 -m imefuse.cli detect      # 先看这台机器上有什么
python3 -m imefuse.cli gui         # 本地界面，浏览器自动打开
make app && open build/imefuse.app # 或者打个双击就能开的 .app
```

零第三方依赖，Python 3.9+ 即可。

## 三家现状

| 输入法 | 导出 | 合并导入 | 行为验证 |
|---|---|---|---|
| 微信键盘 | ✅ | ✅ | ✅ 改计数真的会改变候选排序 |
| 搜狗 | ✅ | ✅ | ✅ 导入的词打拼音直接上屏 |
| 豆包 | ✅ | ✅ | ✅ 导入的词打拼音直接上屏 |

「行为验证」指的是：写进词库 → 重启输入法 → 真的打拼音上屏那个词，而不是「字节写到磁盘上了」。

## 合并规则（不可关闭）

- 只增不删：已有词条永不移除
- 只升不降：`merged >= old`，否则拒绝该条
- 不碰无关条目：不参与合并的键不重写
- 幂等：计数相同判 `keep`，同一份导出重复导入是空操作
- 写前必备份、必退输入法，写完自动重启并回读校验，失败自动回滚

## 一个必须注意的点

三家都会把词库同步到账号云端。**导入期间请断网或退出登录**，否则云端可能覆盖你刚合并的结果。这是唯一我们控制不了的地方。

## 更多文档

- [docs/verification.md](docs/verification.md) —— 完整验证矩阵与证据
- [docs/formats/wetype.md](docs/formats/wetype.md) —— 微信键盘 LevelDB 与 +6 位移
- [docs/formats/sogou.md](docs/formats/sogou.md) —— 搜狗 SGPU 容器与 `O_NONCE` 校验
- [docs/formats/doubao.md](docs/formats/doubao.md) —— 豆包 usr.dat 容器与加密
