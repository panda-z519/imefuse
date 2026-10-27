"""能不能用、能用多少：统一的能力分级。

三个适配器各有各的硬边界 —— 豆包的 usr.dat 是预分配定长容器、搜狗的词库里
根本没有计数器、微信键盘的 userDict 带版本号 —— 这些边界不该等到用户按下
「确认写入」才以 traceback 的形式冒出来。doctor、GUI 状态卡和写入前的预检
都读这里，保证三处说的是同一件事。

三档：
    ready    现在就能读、能写、能合并
    limited  能用，但有明确的上限或代价（逐条写在 limits 里）
    blocked  现在做不了（未安装 / 格式不识别 / 容器已满）

每条限制都带一个稳定的 code，方便脚本判断，也方便以后改文案不改行为。
"""
import glob
import os
import sys

READY, LIMITED, BLOCKED = "ready", "limited", "blocked"

MIN_PYTHON = (3, 9)

_RANK = {READY: 0, LIMITED: 1, BLOCKED: 2}

# 豆包定长容器里一条记录的平均字节数，用于在 record_count 以外的第二道
# 上限（data_capacity）上估剩余容量。实测本机 37.5 B/条；取 24 作为下限，
# 避免短词让估算过于乐观。
_MIN_RECORD_BYTES = 24


def note(code, level, text, detail="", value=None):
    return {"code": code, "level": level, "text": text, "detail": detail,
            "value": value}


def worst(items):
    level = READY
    for item in items:
        if _RANK[item["level"]] > _RANK[level]:
            level = item["level"]
    return level


def _python():
    if sys.version_info >= MIN_PYTHON:
        return []
    return [note("python-version", BLOCKED,
                 "Python %d.%d 低于要求的 %d.%d" % (sys.version_info[0],
                                                    sys.version_info[1],
                                                    MIN_PYTHON[0], MIN_PYTHON[1]),
                 sys.executable)]


def _wetype_dict_versions(row):
    """微信键盘把格式版本写进目录名（userDict/v5），换版本我们就读不到。"""
    base = os.path.join(row.get("data_root") or "", "userDict")
    if not os.path.isdir(base):
        return []
    found = sorted(name for name in os.listdir(base)
                   if len(name) > 1 and name[0] == "v" and name[1:].isdigit()
                   and os.path.isdir(os.path.join(base, name)))
    if not found or "v5" in found:
        return []
    return [note("wetype-dict-version", BLOCKED,
                 "词库目录是 %s，只适配过 v5" % "/".join(found),
                 "厂商改了词库版本号，格式尚未逆向；请等待适配或先用旧版输入法导出")]


def _doubao_capacity(row):
    """豆包的 usr.dat 是定长容器：记录数和字节数都是硬上限。"""
    stats = row.get("stats") or {}
    total = stats.get("index_entries")
    used = stats.get("record_count")
    cap = stats.get("data_capacity")
    used_bytes = stats.get("data_used")
    if not total or used is None or not cap or used_bytes is None:
        return []
    free_records = max(0, int(total) - int(used))
    avg = max(_MIN_RECORD_BYTES, (used_bytes / used) if used else _MIN_RECORD_BYTES)
    free_bytes = max(0, int(cap) - int(used_bytes))
    room = min(free_records, int(free_bytes / avg))
    if room <= 0:
        return [note("doubao-capacity", BLOCKED,
                     "词库已满（%d 条 / %d 字节的定长容器）" % (total, cap),
                     "豆包不会自动扩容；只能先清理词库再导入", value=0)]
    level = LIMITED if room < 500 else READY
    return [note("doubao-capacity", level,
                 "还能再写入约 %d 条（定长容器 %d 条 / %d 字节）" % (room, total, cap),
                 "实测 %d 条占 %d 字节，按 %.0f 字节/条估算"
                 % (used, used_bytes, avg), value=room)]


def capacity_of(row):
    """目标还能写多少条；None 表示没有已知上限。"""
    for item in row.get("limits") or []:
        if item["code"] == "doubao-capacity":
            return item.get("value")
    return None


def _doubao_accounts(row):
    """拿不到登录 uid 时按 mtime 猜账号；多账号机器上会静默写错人。"""
    base = os.path.join(row.get("data_root") or "", "EngineUserDictAccounts")
    dirs = [path for path in glob.glob(os.path.join(base, "*"))
            if os.path.isfile(os.path.join(path, "usr.dat"))]
    if len(dirs) <= 1:
        return []
    from .adapters import doubao
    if doubao.account_uid():
        return []
    return [note("doubao-multi-account", LIMITED,
                 "检测到 %d 个账号词库，将写入最近修改的那个" % len(dirs),
                 "登录信息读不到；如需指定账号，先登录豆包让它写出 owner 文件再试")]


def _doubao_write_semantics(row):
    if not (row.get("caps") or {}).get("has_count"):
        return []
    return [note("doubao-add-only", LIMITED,
                 "写回是只加词：已存在的词不会抬高词频",
                 "usr.dat 的写回路径按词面去重后跳过，count 不落盘")]


def _sogou_counter(row):
    caps = row.get("caps") or {}
    if not caps or caps.get("has_count") is not False:
        return []
    return [note("sogou-no-counter", LIMITED,
                 "词库没有词频计数器，合并只能加词条",
                 caps.get("note", ""))]


def _nearest_existing(path):
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return probe or path


def _backup_writable():
    """没有备份就没有回滚；这比任何写入失败都严重。

    只检查数据目录还不存在的情况是错的正例 —— 首次运行时整个
    ~/Library/Application Support/imefuse 都还没建出来，要往上找到第一个
    存在的目录再判断能否写。
    """
    from . import paths

    target = paths.backups_dir()
    probe = _nearest_existing(target)
    if os.access(probe, os.W_OK):
        return []
    return [note("backup-not-writable", BLOCKED,
                 "备份目录不可写，写入将失去回滚能力",
                 "%s（最近的已存在目录 %s 不可写）" % (target, probe))]


_STATE_NOTES = {
    "missing": ("ime-missing", "本机没装这个输入法"),
    "no-data": ("ime-no-data", "已安装但还没有词库（先打几个字让它生成词库）"),
    "unknown-format": ("ime-format-unknown", "词库格式与逆向结果不一致，已拒绝写入"),
    "error": ("ime-read-error", "读取词库失败"),
}


def assess(ime, row):
    """(level, items) —— row 是 discover.probe() 的返回。"""
    items = list(_python())
    state = row.get("state")
    mapped = _STATE_NOTES.get(state)
    if mapped:
        items.append(note(mapped[0], BLOCKED, mapped[1], row.get("detail", "")))

    if ime == "wetype":
        items += _wetype_dict_versions(row)
    elif ime == "doubao":
        items += _doubao_capacity(row)
        items += _doubao_accounts(row)
        items += _doubao_write_semantics(row)
    elif ime == "sogou":
        items += _sogou_counter(row)

    if state in ("ready", "unknown-format"):
        items += _backup_writable()

    return worst(items), items


def flatten(rows):
    """把每家的 limits 摊平成一串 dict，给 CLI 逐行打印用。"""
    out = []
    for row in rows:
        for item in row.get("limits") or []:
            out.append({"ime": row.get("ime"), "level": item["level"],
                        "code": item["code"], "text": item["text"],
                        "detail": item["detail"]})
    return out
