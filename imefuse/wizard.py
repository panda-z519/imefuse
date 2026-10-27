"""交互向导：一条命令进去，选要做什么，再选对象，逐步执行。

设计取向是"每一步都能反悔、能退回上一步"，而不是一串不可撤销的子命令：
    ./imefuse.sh               # 无参数直接进向导
    ./imefuse.sh wizard        # 显式进入

只依赖标准库与终端行为，不引入 curses：
- 有方向键的终端用 readline 补全/历史；纯数字选择在任何终端都能用。
- 非交互环境（管道、CI，isatty 为假）直接报错退出，绝不读到 EOF 就当成
  默认选项执行 —— 一个向导在无人值守时"自动选择导出并写入"是最糟的结果。
"""
import sys

from . import actions, discover, limits
from . import progress as progress_mod

LEVEL_LABEL = {limits.READY: "就绪", limits.LIMITED: "可用·受限",
               limits.BLOCKED: "不可用"}
LEVEL_MARK = {limits.READY: "·", limits.LIMITED: "!", limits.BLOCKED: "✕"}


class Cancelled(Exception):
    """用户按 Ctrl-C / 输入 q 退出。"""


class Console(object):
    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self._stdin = sys.stdin

    # -- 输出 ----------------------------------------------------------
    def line(self, text=""):
        self.out.write(text + "\n")

    def rule(self, title=""):
        width = 66
        if title:
            pad = max(0, width - _width(title) - 3)
            self.line("── %s %s" % (title, "─" * pad))
        else:
            self.line("─" * width)

    def say(self, text, mark=" "):
        self.line("  %s %s" % (mark, text))

    def note(self, text):
        self.line("    %s" % text)

    def progress(self):
        """进度条写到 stderr，别和 stdout 上的问答/JSON 混在一起。"""
        return progress_mod.make()

    # -- 输入 ----------------------------------------------------------
    def interactive(self):
        try:
            return self._stdin.isatty()
        except (AttributeError, ValueError):
            return False

    def ask(self, prompt, default=None):
        suffix = " [%s]" % default if default not in (None, "") else ""
        try:
            raw = input("  %s%s: " % (prompt, suffix))
        except EOFError:
            raise Cancelled() from None
        except KeyboardInterrupt:
            raise Cancelled() from None
        raw = raw.strip()
        if not raw:
            return default
        return raw

    def choose(self, title, options, allow_back=False, default=1,
               back_label="返回上一步"):
        """options: [(value, label, hint, disabled_reason)]。

        返回选中的 value；选到 disabled 的会说明原因并重问。
        allow_back 时输入 b 返回 None。
        """
        self.line()
        self.line("  %s" % title)
        for index, (_value, label, hint, blocked) in enumerate(options, 1):
            mark = " " if not blocked else "×"
            tail = ("（%s）" % hint) if hint else ""
            if blocked:
                tail = "（不可选：%s）" % blocked
            self.line("   %s %d) %s%s" % (mark, index, label, tail))
        if allow_back:
            self.line("   b) %s" % back_label)
        self.line("   q) 退出")
        while True:
            raw = self.ask("选择编号", default=str(default))
            if raw in ("q", "quit", "exit"):
                raise Cancelled()
            if allow_back and raw in ("b", "back"):
                return None
            try:
                picked = int(raw)
            except (TypeError, ValueError):
                self.say("请输入编号（q 退出）", "!")
                continue
            if not 1 <= picked <= len(options):
                self.say("没有 %d 这个编号" % picked, "!")
                continue
            value, label, _hint, blocked = options[picked - 1]
            if blocked:
                self.say("%s 现在不可选：%s" % (label, blocked), "!")
                continue
            return value

    def confirm(self, prompt, default=False):
        raw = self.ask("%s (%s)" % (prompt, "Y/n" if default else "y/N"),
                       "y" if default else "n")
        return str(raw).strip().lower() in ("y", "yes", "是", "1", "true")


def _width(text):
    total = 0
    for ch in text:
        total += 2 if ord(ch) > 0x2E80 else 1
    return total


# ------------------------------------------------------------------ 展示

def show_status(con, rows):
    con.rule("本机状态")
    for row in rows:
        con.line("  %-8s %-10s %s %s" % (
            row["ime"], LEVEL_LABEL.get(row["level"], row["level"]),
            "v" + (row["version"] or "?"),
            _count_text(row)))
        for item in row["limits"]:
            con.note("%s %s" % (LEVEL_MARK.get(item["level"], "·"), item["text"]))
    usable = [r["ime"] for r in rows if r["level"] != limits.BLOCKED]
    con.line()
    con.say("可选目标：%s" % (", ".join(usable) if usable else "无"))


def _count_text(row):
    if row.get("records") is None:
        return "—"
    return "%d 条" % row["records"]


def _target_options(con, rows, need_write):
    options = []
    for row in rows:
        blocked = None
        if row["state"] != "ready":
            blocked = row["detail"] or row["state"]
        elif need_write and row["level"] == limits.BLOCKED:
            blocked = "；".join(n["text"] for n in row["limits"]
                                if n["level"] == limits.BLOCKED)
        hint = "%s · %s" % (LEVEL_LABEL.get(row["level"], row["level"]), _count_text(row))
        if row["level"] == limits.LIMITED and not blocked:
            room = limits.capacity_of(row)
            if room is not None:
                hint += " · 还能写约 %d 条" % room
        options.append((row["ime"], row["label"], hint, blocked))
    return options


def show_diff(con, result):
    summary = result.get("summary") or {}
    con.line()
    con.say("新增 %(add)d · 累加 %(bump)d · 同词异音 %(conflict)d · "
            "不变 %(keep)d · 删除 %(deleted)d · 降频 %(lowered)d" % summary)
    rows = result.get("rows") or []
    for item in rows[:12]:
        con.note("%-6s %-10s %-6s 旧 %-5s 新 %-5s → %s" % (
            item["w"], item["py"], item["action"], item["old"], item["new"], item["merged"]))
    if len(rows) > 12:
        con.note("… 另有 %d 条未显示" % (len(rows) - 12))
    if result.get("truncated"):
        con.note("还有 %d 条已在计算中省略" % result["truncated"])


def show_export(con, result):
    con.line()
    con.say("已导出 %d 条 → %s" % (result["count"], result["file"]))
    con.note("同时写了 %s（纯文本，便于查看）" % result["tsv"])
    for item in result["top"][:5]:
        con.note("%s  %s  %s" % (item["w"], item["py"], item["n"]))


def show_write(con, result):
    con.line()
    if result.get("note") == "nothing to write":
        con.say("没有需要写入的条目")
        return
    con.say("写入 %d / %d 条" % (result.get("written", 0), result.get("requested", 0)))
    if result.get("backup"):
        con.note("备份：%s" % result["backup"])
    if result.get("failed"):
        con.note("失败 %d 条：%s" % (len(result["failed"]), result["failed"][:5]))


# ------------------------------------------------------------------ 动作

def action_status(con, state):
    show_status(con, state["rows"])
    return True


def action_export(con, state):
    rows = state["rows"]
    ime = con.choose("导出哪个输入法的个人词？",
                     _target_options(con, rows, need_write=False))
    # 导出是单次阻塞调用，中间没有可观测边界 —— 只给耗时，不画百分比
    with con.progress().step("读取 %s 的词库" % discover.BY_NAME[ime]["label"]) as st:
        result = actions.export_ime(ime)
        st.note("%d 条" % result["count"])
    show_export(con, result)
    state["last_export"] = result
    if con.confirm("顺便把它合并进另一个输入法吗？"):
        return migrate_to(con, state, source=ime)
    return True


def migrate_to(con, state, source=None):
    rows = state["rows"]
    if source is None:
        source = con.choose("从哪个输入法出发？",
                            _target_options(con, rows, need_write=False))
    targets = [r for r in rows if r["ime"] != source]
    target = con.choose("合并进哪个输入法？",
                        _target_options(con, targets, need_write=True))
    if target is None:
        return True
    with con.progress().step("读取源词库并计算 diff") as st:
        result = actions.migrate(source, target, apply=False)
        summary = result.get("summary") or {}
        st.note("新增 %d · 累加 %d" % (summary.get("add", 0), summary.get("bump", 0)))
    show_diff(con, result)
    if not (result.get("summary") or {}).get("add", 0) and \
            not (result.get("summary") or {}).get("bump", 0):
        con.say("没有需要写入的条目，到此为止。")
        return True
    if not con.confirm("确认写入 %s？会先备份、退出输入法、写完重启。"
                       % discover.BY_NAME[target]["label"]):
        con.say("已取消，没有写入任何东西。")
        return True
    show_write(con, actions.migrate(source, target, apply=True,
                                    progress=con.progress()))
    state["rows"] = discover.probe_all()
    return True


def action_migrate(con, state):
    return migrate_to(con, state)


def action_import(con, state):
    rows = state["rows"]
    exports = actions.list_exports()
    if not exports:
        con.say("exports/ 里还没有导出文件；先用「导出」生成一份，或把 .jsonl 放进去。")
        return True
    options = [(item["path"],
                item["file"],
                "%d 条 · 来自 %s" % (item["count"], item["source"] or "?"),
                None) for item in exports[:15]]
    path = con.choose("选一份要导入的文件", options)
    target = con.choose("合并进哪个输入法？",
                        _target_options(con, rows, need_write=True))
    with con.progress().step("读取文件并计算 diff") as st:
        result = actions.diff_file(target, path)
        summary = result.get("summary") or {}
        st.note("新增 %d · 累加 %d" % (summary.get("add", 0), summary.get("bump", 0)))
    show_diff(con, result)
    if not con.confirm("确认写入 %s？" % discover.BY_NAME[target]["label"]):
        con.say("已取消，没有写入任何东西。")
        return True
    show_write(con, actions.apply_file(target, path, progress=con.progress()))
    state["rows"] = discover.probe_all()
    return True


def action_backup(con, state):
    rows = [r for r in state["rows"] if r["installed"]]
    if not rows:
        con.say("没有已安装的输入法。")
        return True
    ime = con.choose("备份哪个输入法？", _target_options(con, rows, need_write=False))
    with con.progress().step("整目录快照（rsync）") as st:
        meta = actions.snapshot(ime, label="manual")
        st.note(meta.get("size", ""))
    con.line()
    con.say("已备份到 %s（%s）" % (meta["path"], meta["size"]))
    return True


def action_restore(con, state):
    items = actions.backups()
    if not items:
        con.say("还没有备份。")
        return True
    options = [(item["name"],
                item.get("at", item["name"]),
                "%s · %s · %s" % (item.get("ime", "?"), item.get("label", ""),
                                  item.get("size", "")),
                None) for item in items[:15]]
    name = con.choose("回滚到哪个备份？", options)
    con.line()
    con.say("这会覆盖该输入法当前的词库。")
    if not con.confirm("确认回滚？"):
        con.say("已取消。")
        return True
    con.say(str(actions.restore(name)))
    state["rows"] = discover.probe_all()
    return True


def action_doctor(con, state):
    con.rule("能力与限制")
    for row in state["rows"]:
        con.line("  %-8s %-10s %s" % (row["ime"],
                                      LEVEL_LABEL.get(row["level"], row["level"]),
                                      row["label"]))
        if not row["limits"]:
            con.note("无已知限制")
        for item in row["limits"]:
            con.note("%s %s" % (LEVEL_MARK.get(item["level"], "·"), item["text"]))
            if item["detail"]:
                con.line("      %s" % item["detail"])
        con.line()
    return True


MENU = [
    ("status", "查看状态", "每家能不能用、有多少词、有哪些硬限制", action_status),
    ("export", "导出", "把某家的个人词导出成 .jsonl / .tsv", action_export),
    ("migrate", "一键迁移", "从一家导出并合并进另一家（先看 diff 再写）", action_migrate),
    ("import", "合并导入", "把 exports/ 里的一份文件合并进某家", action_import),
    ("backup", "备份", "整目录快照某家的词库", action_backup),
    ("restore", "回滚", "把某家恢复到某个备份", action_restore),
    ("doctor", "限制说明", "逐条列出每家的能力档与原因", action_doctor),
]


def run(refresh=True):
    con = Console()
    if not con.interactive():
        con.line("imefuse: 交互向导需要终端。")
        con.line("非交互请直接用子命令：./imefuse.sh --help")
        return 2

    con.rule("imefuse")
    con.line("  词库在本机，不联网、不上传。写入前会先备份并退出输入法。")
    # 首次探测要解析三家词库（微键 20 万条键值约 5 秒），先给个交代
    with con.progress().step("探测本机输入法") as st:
        state = {"rows": discover.probe_all()}
        ready = [r["ime"] for r in state["rows"] if r["level"] != limits.BLOCKED]
        st.note("%d 家可用" % len(ready))

    try:
        return _loop(con, state)
    except Cancelled:
        con.line()
        con.say("已退出。")
        return 0


def _loop(con, state):
    first = True
    while True:
        # 首轮上方已经有欢迎语，不再额外空一行
        if not first:
            con.line()
        first = False
        options = [(key, label, hint, None) for key, label, hint, _fn in MENU]
        picked = con.choose("要做什么？（q 退出）", options, allow_back=False)
        handler = dict((key, fn) for key, _l, _h, fn in MENU)[picked]
        keep_going = True
        try:
            keep_going = handler(con, state)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            con.line()
            con.say("失败了：%s" % exc, "✕")
            if isinstance(exc, (RuntimeError, ValueError)):
                con.note("没有写入任何东西。")
        if not keep_going:
            return 0
        if picked not in ("status", "doctor"):
            # 写入/导出之后状态可能变了（词条数、剩余容量），重新探测
            state["rows"] = discover.probe_all()
        if not con.confirm("继续？", default=True):
            con.line()
            con.say("再见。")
            return 0
