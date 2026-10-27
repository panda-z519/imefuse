"""动作层：导出一个输入法、看 diff、合并写入、一键迁移、备份回滚。

这些原本住在 gui/server.py 里，结果是 CLI 和向导想用同一段逻辑只能抄一遍
（CLI 的 import 就是这么漂移成"不备份、不退输入法"的）。现在只有这一份，
GUI 的 HTTP 处理器、CLI 子命令、交互向导都调这里的函数。

约定：
- 一切写入都先整目录快照、退出输入法、写完重启；失败也重启。
- 写入前的可用性判断统一走 limits，不各自 if 一遍 state。
"""
import os
import time

from . import backup, canonical, discover, limits
from . import merge as merge_mod
from . import paths
from . import progress as progress_mod

SAFE_NAME_EXPORTS = "exports"


def _now():
    return int(time.time())


def label_of(ime):
    return discover.BY_NAME[ime]["label"]


def require_usable(ime):
    """不阻塞（ready / limited）就放行，blocked 直接拒绝并说明原因。"""
    row = discover.probe(ime)
    if row["level"] == limits.BLOCKED:
        reasons = "；".join(n["text"] for n in row["limits"]
                            if n["level"] == limits.BLOCKED) or row["detail"]
        raise RuntimeError("%s 当前不可用：%s" % (row["label"], reasons))
    return row


def require_ready(ime):
    """需要真的能读（导出、读目标词库）时用这个，比 require_usable 严格。"""
    row = discover.probe(ime)
    if row["state"] != "ready":
        raise RuntimeError("%s 当前不可用：%s" % (row["label"], row["detail"] or row["state"]))
    return row


def _has_count(ime):
    return bool(getattr(discover.get_adapter(ime), "CAPS", {}).get("has_count", True))


def exports_dir():
    path = paths.exports_dir()
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------ 导出

def export_ime(ime):
    """导出一个输入法到 exports/，返回文件与统计。"""
    require_ready(ime)
    mod = discover.get_adapter(ime)
    records, stats = mod.export()
    records = canonical.dedupe(records)
    header = canonical.make_header(ime, count=len(records))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = exports_dir()
    jsonl = os.path.join(folder, "%s-%s.jsonl" % (ime, stamp))
    tsv = os.path.join(folder, "%s-%s.tsv" % (ime, stamp))
    canonical.write_jsonl(jsonl, header, records)
    canonical.write_tsv(tsv, records)
    top = sorted(records, key=lambda r: -r.get("n", 0))[:30]
    return {"ime": ime, "file": os.path.basename(jsonl), "tsv": os.path.basename(tsv),
            "path": jsonl, "count": len(records), "stats": stats,
            "export_id": header["export_id"],
            "top": [{"w": r["w"], "py": r.get("py", ""), "n": r.get("n", 0)} for r in top]}


def list_exports():
    """exports/ 里已有的导出，最新在前。"""
    folder = paths.exports_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(folder, name)
        try:
            header, recs = canonical.read_jsonl(path)
        except Exception:  # noqa: BLE001
            continue
        out.append({"file": name, "path": path, "count": len(recs),
                    "source": (header or {}).get("source"),
                    "at": (header or {}).get("exported_at")})
    return out


def load_file(path):
    """读一份 canonical 文件（jsonl 或 tsv），返回 (header, records)。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if path.lower().endswith(".tsv"):
        return canonical.read_tsv(path)
    return canonical.read_jsonl(path)


# ------------------------------------------------------------------ 看 diff

def diff_records(ime, incoming_list, strategy="P1", alpha=0.5):
    require_ready(ime)
    incoming = {canonical.merge_key(r): r for r in incoming_list}
    records, _stats = discover.get_adapter(ime).export()
    existing = {canonical.merge_key(r): r for r in records}
    plan, summary = merge_mod.plan_merge(existing, incoming, strategy=strategy, alpha=alpha,
                                         target_has_count=_has_count(ime))
    todo = [p for p in plan if p["action"] in ("add", "bump", "conflict")]
    todo.sort(key=lambda p: -p["merged"])
    return {"summary": summary,
            "rows": [{"w": p["key"][0], "py": p["key"][1], "action": p["action"],
                      "old": p["old"], "new": p["new"], "merged": p["merged"],
                      "note": p.get("note", "")} for p in todo[:200]],
            "truncated": max(0, len(todo) - 200)}


def diff_file(ime, path, strategy="P1", alpha=0.5):
    _header, incoming_list = load_file(path)
    return diff_records(ime, incoming_list, strategy, alpha)


# ------------------------------------------------------------------ 写入

def apply_records(ime, incoming_list, header=None, strategy="P1", alpha=0.5,
                  force_unsafe=False, progress=None):
    """把一份词表合并进目标输入法。

    有 7 个离散阶段，全部真实存在（不是把一次调用切碎凑数），所以这里用
    n/N 的形式报进度。重活是「读取现有词库」和「写入」两段。
    """
    progress = progress or progress_mod.null()
    # 调用方可能已经定过总数（migrate 是 1 读源 + 7 目标）；不要覆盖，否则重数
    if not getattr(progress, "total_count", 0):
        progress = progress.total(7)
    with progress.step("检查目标输入法") as st:
        row = require_usable(ime)
        caps = row.get("caps") or {}
        if not caps.get("writable", True) and not force_unsafe:
            raise RuntimeError("该输入法写回未通过引擎校验，已拒绝写入："
                               + caps.get("note", ""))
        st.note(discover.BY_NAME[ime]["label"])

    with progress.step("读取目标现有词库") as st:
        mod = discover.get_adapter(ime)
        records, _stats = mod.export()
        st.note("%d 条" % len(records))
    existing = {canonical.merge_key(r): r for r in records}
    incoming = {canonical.merge_key(r): r for r in incoming_list}

    with progress.step("计算合并计划") as st:
        plan, summary = merge_mod.plan_merge(
            existing, incoming, strategy=strategy, alpha=alpha,
            target_has_count=_has_count(ime))
        todo = [p for p in plan if p["action"] in ("add", "bump")]
        st.note("新增 %d · 累加 %d" % (summary["add"], summary["bump"]))
    if not todo:
        return {"summary": summary, "written": 0, "note": "nothing to write"}

    # 容量红线提前算：豆包是定长容器，写爆了会抛在 write_entries 里，
    # 那时输入法已经被退出、快照也做完了。
    room = limits.capacity_of(row)
    if room is not None and len(todo) > room:
        raise RuntimeError(
            "目标只能再写入约 %d 条，这次要写 %d 条；请先清理词库或分批导入"
            % (room, len(todo)))

    with progress.step("备份整个词库目录") as st:
        snap = backup.snapshot(ime, label="pre-import")
        st.note(snap.get("size", ""))

    with progress.step("退出输入法") as st:
        stopped = backup.quit_ime(ime)
        st.note("已退出" if stopped else "本来就未运行")

    entries = [(p["key"][1], p["key"][0], p["merged"], p.get("ts") or _now())
               for p in todo]
    try:
        with progress.step("写入 %d 条" % len(todo)):
            plan_w, seq = mod.write_entries(entries, dry_run=False)
            ok = sum(1 for p in plan_w if p.get("status") in ("ok", "add"))
            failed = [p.get("key") or p.get("w") for p in plan_w
                      if p.get("status") not in ("ok", "add")]
    finally:
        with progress.step("重启输入法"):
            backup.start_ime(ime)

    return {"summary": summary, "written": ok,
            "requested": len(todo), "failed": failed[:20],
            "seq": seq, "backup": snap["path"], "stopped": stopped,
            "export_id": (header or {}).get("export_id")}


def apply_file(ime, path, strategy="P1", alpha=0.5, force_unsafe=False,
               progress=None):
    header, incoming_list = load_file(path)
    return apply_records(ime, incoming_list, header, strategy, alpha,
                         force_unsafe, progress)




# ------------------------------------------------------------------ 迁移

def migrate(source, target, strategy="P1", alpha=0.5, apply=False, progress=None):
    """一步到位：导出 source，把结果合并进 target。

    apply 时总阶段数是「导出源 1 步 + 目标侧 7 步」，所以要先把同一个
    progress 的 total 定好再往下走，否则编号会在中途重新数一遍。
    """
    progress = progress or progress_mod.null()
    progress = progress.total(8 if apply else 1)

    with progress.step("读取源词库 %s" % discover.BY_NAME[source]["label"]) as st:
        require_ready(source)
        require_usable(target)
        if source == target:
            raise RuntimeError("源和目标不能是同一个输入法")
        records, stats = discover.get_adapter(source).export()
        records = canonical.dedupe(records)
        st.note("%d 条" % len(records))

    if not apply:
        return diff_records(target, records, strategy, alpha)

    # 目标侧的 6 个阶段由 apply_records 负责；它拿到的是已经 total 过的对象，
    # 所以这里把总数补齐成 7 之后传下去。
    header = canonical.make_header(source, count=len(records))
    out = apply_records(target, records, header, strategy, alpha,
                        progress=progress)
    out.update({"source": source, "source_records": len(records),
                "source_stats": stats, "dry_run": False})
    return out


# ------------------------------------------------------------------ 备份

def backups():
    return backup.list_backups()


def snapshot(ime, label="manual"):
    return backup.snapshot(ime, label=label)


def restore(name, ime=None):
    return backup.restore(name, ime)
