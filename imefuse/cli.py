"""imefuse command line: export / import / diff / gui."""
import argparse
import json
import os
import sys
import time

from . import actions, limits as limits_mod
from . import discover, merge as merge_mod, paths, util
from . import progress as progress_mod
from .adapters import doubao, sogou, wetype

__version__ = "0.1.0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = paths.exports_dir()
STATE_DIR = paths.state_dir()
RUNS_DIR = paths.runs_dir()

ADAPTERS = {
    "wetype": wetype,
    "sogou": sogou,
    "doubao": doubao,
}


def _ensure_dirs():
    for d in (EXPORT_DIR, STATE_DIR, RUNS_DIR):
        os.makedirs(d, exist_ok=True)



def cmd_export(args):
    _ensure_dirs()
    with progress_mod.make().step("读取 %s 的词库" % discover.BY_NAME[args.ime]["label"]) as st:
        result = actions.export_ime(args.ime)
        st.note("%d 条" % result["count"])
    print(json.dumps({
        "ime": args.ime, "records": result["count"], "stats": result["stats"],
        "jsonl": result["file"], "tsv": result["tsv"],
        "export_id": result["export_id"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_import(args):
    """与 GUI / 向导共用 actions：快照 → 退输入法 → 写 → 重启。"""
    _ensure_dirs()
    try:
        header, incoming_list = actions.load_file(args.file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "读不了这份文件：%s" % exc,
                          "file": args.file,
                          "hint": "文件要存在，且是 imefuse 导出的 .jsonl 或 .tsv"},
                         ensure_ascii=False, indent=2))
        return 2
    report = {"ime": args.ime, "strategy": args.strategy,
              "dry_run": not args.apply, "file": os.path.basename(args.file)}

    if not args.apply:
        result = actions.diff_records(args.ime, incoming_list, args.strategy, args.alpha)
        report["summary"] = result["summary"]
        report["to_write"] = result["summary"]["add"] + result["summary"]["bump"]
        report["sample"] = result["rows"][:10]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    try:
        result = actions.apply_records(args.ime, incoming_list, header,
                                       args.strategy, args.alpha, args.force_unsafe,
                                       progress=progress_mod.make())
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc), "ime": args.ime},
                         ensure_ascii=False, indent=2))
        return 3

    report.update({"summary": result.get("summary"),
                   "written": result.get("written", 0),
                   "requested": result.get("requested", 0),
                   "failed": result.get("failed", []),
                   "seq": result.get("seq"), "backup": result.get("backup"),
                   "note": result.get("note")})
    ledger = os.path.join(STATE_DIR, "imported.json")
    hist = util.read_json(ledger) if os.path.exists(ledger) else []
    hist.append({"ime": args.ime, "export_id": (header or {}).get("export_id"),
                 "file": os.path.basename(args.file), "at": int(time.time()),
                 "written": report["written"], "strategy": args.strategy})
    util.write_json(ledger, hist, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_wizard(args):
    from . import wizard
    return wizard.run()


STATE_LABEL = {
    "ready": "可用",
    "missing": "未安装",
    "no-data": "无词库",
    "unknown-format": "格式不识别",
    "error": "读取失败",
}

LEVEL_LABEL = {
    "ready": "就绪",
    "limited": "可用·受限",
    "blocked": "不可用",
}
LEVEL_MARK = {"ready": "· ", "limited": "! ", "blocked": "x "}


def cmd_detect(args):
    rows = discover.probe_all()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    print("%-8s %-10s %-9s %-7s %-6s %s" % ("输入法", "能力", "版本", "条目", "运行", "说明"))
    print("-" * 96)
    for r in rows:
        print("%-8s %-10s %-9s %-7s %-6s %s" % (
            r["ime"], LEVEL_LABEL.get(r["level"], r["level"]),
            r["version"] or "-", r["records"] if r["records"] is not None else "-",
            "是" if r["running"] else "否", r["detail"]))
    blocked = [r for r in rows if r["level"] == "blocked"]
    usable = discover.usable(rows)
    print("\n可迁移: %s" % (", ".join(usable) if usable else "无"))
    if blocked:
        print("不可用: %s（原因见 imefuse doctor）"
              % ", ".join(r["ime"] for r in blocked))

    limits = limits_mod.flatten(rows)
    if limits:
        print()
        for row in limits:
            print("%s%-8s %s" % (LEVEL_MARK.get(row["level"], "  "),
                                 row["ime"], row["text"]))
    return 0


def cmd_doctor(args):
    rows = discover.probe_all()
    if args.json:
        out = [{"ime": r["ime"], "level": r["level"], "state": r["state"],
                "limits": r["limits"]} for r in rows]
        print(json.dumps({"checks": out, "usable": discover.usable(rows)},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    print("imefuse %s · Python %s · %s"
          % (__version__, sys.version.split()[0], paths.data_dir()))
    print()
    for r in rows:
        print("%-8s %-10s %s" % (r["ime"], LEVEL_LABEL.get(r["level"], r["level"]),
                                 r["label"]))
        if not r["limits"]:
            print("         · 无已知限制")
        for item in r["limits"]:
            print("         %s%s" % (LEVEL_MARK.get(item["level"], "  "), item["text"]))
            if item["detail"] and args.verbose and item["detail"] != item["text"]:
                print("           %s" % item["detail"])
        print()
    usable = discover.usable(rows)
    print("可迁移: %s" % (", ".join(usable) if usable else "无"))
    return 0 if usable else 1


def cmd_status(args):
    rows = discover.probe_all()
    out = {}
    for row in rows:
        name = row["ime"]
        out[name] = {k: row.get(k) for k in
                     ("installed", "version", "running", "records", "state",
                      "detail", "caps", "level", "limits")}
        if row.get("stats"):
            out[name]["stats"] = row["stats"]
    out["usable"] = discover.usable(rows)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_gui(args):
    from .gui import server
    return server.main(args.port)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="imefuse",
        description="不带子命令直接进交互向导；也可以走子命令做单步操作。")
    parser.add_argument("--version", action="version", version="imefuse " + __version__)
    # 不 required：无参数时落到向导，而不是打印一大段 usage
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("export", help="export one IME to canonical jsonl/tsv")
    p.add_argument("ime", choices=sorted(ADAPTERS))
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="merge a canonical file into one IME")
    p.add_argument("ime", choices=sorted(ADAPTERS))
    p.add_argument("file")
    p.add_argument("--strategy", default="P1", choices=sorted(merge_mod.STRATEGIES))
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    p.add_argument("--force-unsafe", action="store_true",
                   help="allow writing into an IME whose write path failed verification")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("detect", help="detect which IMEs exist on this Mac")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("wizard", help="交互向导：一条命令，选项式操作")
    p.set_defaults(func=cmd_wizard)

    p = sub.add_parser("doctor", help="每家的能力分级与已知限制")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("gui")
    p.add_argument("--port", type=int, default=8770)
    p.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        # 没有子命令 = 想要向导
        return cmd_wizard(args)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
