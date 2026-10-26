# 微信键盘 WeType 2.2.0 —— 用户词库/词频格式逆向

状态：**读写全链路打通，行为级验证通过**

## 1. 存储位置

```
~/Library/Application Support/WeType/
├── userDict/v5/<dbid>/           # 用户词库主库（LevelDB）
│   └── db_path.conf  -> 当前 dbid
├── userDict/user_hot_word/       # 输入会话日志（LevelDB）
├── DataBase/common.db            # 自定义头加密，非 sqlite
└── mmkv/*                        # MMKV 设置项
```

`<dbid>` 当前为 `1782125319427452`，取自 `userDict/v5/db_path.conf`。

## 2. 编码：整体 +6 字节位移

LevelDB 里 **key 是明文，value 每个字节 +6（mod 256）**。解码即 `byte - 6`。

验证样本：

| 位置 | 原始字节 | 解码后 |
|---|---|---|
| `!v2u!` 记录的 key 部分 | `ngo2yno` | `hai,shi` |
| `!v2u!` 记录 | `u{2}gt` | `ou,an` |
| 会话日志 JSON | `(qotj(@62(zk~z(@(` | `"kind":0,"text":"` |
| 词面 UTF-8 | `\xee\xbb\xb6` | `\xe8\xb5\xb0` = 走 |

注意 `!dcact!` / `!v2u!` 这类命名空间前缀是**明文**，只有 payload 被位移。

## 3. Key 命名空间（35015 条实测）

| 前缀 | 条数 | 含义 |
|---|---|---|
| `!dcact!<id>` | 28911 | 引擎逐条记录（拼音 + 词 + 时间/计数） |
| `!v2u!<pinyin>` | 2178 | **用户词频表：拼音 → 词 + 计数 + 时间** ← 主力 |
| `!u_d_v_p!<pinyin>\x01<word>` | 1988 | 用户词索引（key 里直接带拼音和词） |
| `!user_bigram2!...` | 1707 | 上下文二元 |
| `!en_fe!` / `!env2!` | 228 | 英文统计 |
| `!prof!` / `!reii!` | 3 | 元数据/上报 |

## 4. 记录格式（通用尾部）

所有词条 value 解码后都以固定 8 字节尾部结束：

```
[len-8]        (len - 14) & 0xFF      ← 长度校验字节
[len-7:len-4]  0xFA 0xFA 0xFA
[len-4:len]    0x54 0x9F 0x54 0x9F
```

35015 条里 35013 条满足（仅 2 条 `!prof!` 统计记录例外）。这条规则是判断「这条记录是否合法」的廉价校验。

## 5. `!v2u!<pinyin>` 记录布局

```
[0]              0x00
[1]              词面字节长度 wlen
[2]              0x00
[3:7]            计数 count (u32 LE)
[7:11]           最后使用时间 (u32 LE, unix)
[11:15]          副计数 (u32 LE)
[15 : n-8-wlen]  零/浮点区（权重等）
[n-8-wlen : n-8] 词面 UTF-8
[n-8]            (n-14) & 0xFF
[n-7:n-4]        0xFA 0xFA 0xFA
[n-4:n]          0x54 0x9F 0x54 0x9F
```

key 里的拼音用**逗号**分隔音节：`!v2u!an,niu` → 拼音 `an niu`。

导出实测（按 count 降序）：

```
263   得      201   我      106   事
 91   这个     83   了       81   需要
 79   你       56   一下     47   要
```

## 6. 写入方式：向 WAL 追加 WriteBatch

不需要重写 SSTable 或 MANIFEST：

1. `pkill -TERM` 停掉 `WeType.app/Contents/MacOS/WeType`，等进程消失（LevelDB 的 LOCK 必须释放）
2. 从现有 SSTable + WAL 里取 `max(seq)`，新 batch 用 `seq+1`
3. 构造 WriteBatch：`<seq:u64><count:u32>` + 每条 `0x01 <keylen varint> <key> <vallen varint> <value>`
4. 按 32 KiB 块边界切成 `FULL/FIRST/MIDDLE/LAST` 记录，头部 `crc32c(header+payload)` 再套 LevelDB 的 mask `((crc>>15)|(crc<<17)) + 0xa282ead8`
5. 追加到当前 `.log` 文件，`open -a` 重启输入法

新词条用「同词面字节长度的既有记录」当模板克隆，只替换 count / 时间 / 词面 / 长度校验字节 —— 这样零/浮点区保持合法。

实现：`imefuse/leveldb/{reader,writer,crc32c,snappy}.py`、`imefuse/adapters/wetype.py`

## 7. 行为级验证（已完成）

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | 注入 `!v2u!ce,shi,ci,ku` = 测试词库 / 500 | 重启后 WeType 自己做了 compaction（新 .ldb + 新 MANIFEST），记录存活 |
| 2 | TextEdit 里打 `ceshiciku` 上屏 | 上屏文本 = **测试词库** |
| 3 | 合并导入「合并测试词」count=42 | 打 `hebingceshici` 上屏 = 合并测试**次**（系统词排前面） |
| 4 | 把「合并测试词」count 提到 50000 再合并 | 打 `hebingceshici` 上屏 = 合并测试**词** ✅ |

第 3→4 步证明：**写进去的计数真的会改变候选排序**，这正是「词频迁移」成立的前提。

## 8. 已知限制

- 写库必须停进程；WeType 运行中改 LevelDB 会损坏。
- `!dcact!` 逐条记录我没有完整解码（中间有未知字段），目前只做只读解析；写回只走 `!v2u!`。
- `DataBase/common.db` 是加密的，未解析（不影响词库迁移）。
- 三家都开着云同步，导入后如果云端下发会覆盖本地 —— 迁移期间要暂停同步。
