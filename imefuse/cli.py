"""imefuse command line: export / import / diff / gui."""
import argparse
import json
import os
import subprocess
import sys
import time

from . import canonical, merge as merge_mod
from . import discover, util
from .adapters import doubao, sogou, wetype

__version__ = "0.1.0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(ROOT, "exports")
STATE_DIR = os.path.join(ROOT, "state")
RUNS_DIR = os.path.join(ROOT, "runs")

ADAPTERS = {
    "wetype": wetype,
    "sogou": sogou,
    "doubao": doubao,
}


def target_has_count(ime):
    return bool(getattr(ADAPTERS[ime], "CAPS", {}).get("has_count", True))


def _ensure_dirs():
    for d in (EXPORT_DIR, STATE_DIR, RUNS_DIR):
        os.makedirs(d, exist_ok=True)


def _ime_running(process_marker):
    out = subprocess.run(["pgrep", "-f", process_marker], capture_output=True, text=True)
    return bool(out.stdout.strip())


def cmd_export(args):
    _ensure_dirs()
    adapter = ADAPTERS[args.ime]
    records, stats = adapter.export()
    records = canonical.dedupe(records)
    header = canonical.make_header(args.ime, count=len(records))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl = os.path.join(EXPORT_DIR, "%s-%s.jsonl" % (args.ime, stamp))
    tsv = os.path.join(EXPORT_DIR, "%s-%s.tsv" % (args.ime, stamp))
    canonical.write_jsonl(jsonl, header, records)
    canonical.write_tsv(tsv, records)
    print(json.dumps({
        "ime": args.ime, "records": len(records), "stats": stats,
        "jsonl": os.path.relpath(jsonl, ROOT), "tsv": os.path.relpath(tsv, ROOT),
        "export_id": header["export_id"],
    }, ensure_ascii=False, indent=2))
    return 0


def _load_existing(ime):
    records, _stats = ADAPTERS[ime].export()
    return {canonical.merge_key(r): r for r in records}


def cmd_import(args):
    _ensure_dirs()
    adapter = ADAPTERS[args.ime]
    header, incoming_list = canonical.read_jsonl(args.file)
    incoming = {canonical.merge_key(r): r for r in incoming_list}
    existing = _load_existing(args.ime)
    plan, summary = merge_mod.plan_merge(existing, incoming, strategy=args.strategy,
                                         alpha=args.alpha,
                                         target_has_count=target_has_count(args.ime))
    todo = [p for p in plan if p["action"] in ("add", "bump")]
    report = {
        "ime": args.ime, "strategy": args.strategy, "dry_run": not args.apply,
        "summary": summary, "to_write": len(todo),
        "sample": [{"w": p["key"][0], "py": p["key"][1], "action": p["action"],
                    "old": p["old"], "new": p["new"], "merged": p["merged"]}
                   for p in sorted(todo, key=lambda x: -x["merged"])[:10]],
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not getattr(ADAPTERS[args.ime], "CAPS", {}).get("writable", True) and not args.force_unsafe:
        print(json.dumps({
            "error": "该输入法写回未通过引擎校验，默认拒绝写入",
            "ime": args.ime,
            "reason": getattr(ADAPTERS[args.ime], "CAPS", {}).get("note", ""),
            "override": "--force-unsafe（有丢词风险，先做备份）",
        }, ensure_ascii=False, indent=2))
        return 3
    if not args.force and _ime_running(args.process_marker):
        print(json.dumps({"error": "IME is running; quit it first or pass --force",
                          "marker": args.process_marker}, ensure_ascii=False, indent=2))
        return 2
    now = int(time.time())
    entries = [(p["key"][1], p["key"][0], p["merged"], p.get("ts") or now) for p in todo]
    plan_w, seq = adapter.write_entries(entries, dry_run=False)
    ok = sum(1 for p in plan_w if p.get("status") in ("ok", "add"))
    report["written"] = ok
    report["seq"] = seq
    report["failed"] = [p.get("key") or p.get("w") for p in plan_w
                        if p.get("status") not in ("ok", "add")]
    ledger = os.path.join(STATE_DIR, "imported.json")
    hist = []
    if os.path.exists(ledger):
        hist = util.read_json(ledger)
    hist.append({"ime": args.ime, "export_id": (header or {}).get("export_id"),
                 "file": os.path.basename(args.file), "at": now,
                 "written": ok, "strategy": args.strategy})
    util.write_json(ledger, hist, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


STATE_LABEL = {
    "ready": "可用",
    "missing": "未安装",
    "no-data": "无词库",
    "unknown-format": "格式不识别",
    "error": "读取失败",
}


def cmd_detect(args):
    rows = discover.probe_all()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    print("%-8s %-12s %-9s %-7s %-8s %s" % ("输入法", "状态", "版本", "条目", "运行", "说明"))
    print("-" * 92)
    for r in rows:
        print("%-8s %-12s %-9s %-7s %-8s %s" % (
            r["ime"], STATE_LABEL.get(r["state"], r["state"]),
            r["version"] or "-", r["records"] if r["records"] is not None else "-",
            "是" if r["running"] else "否", r["detail"]))
    usable = [r["ime"] for r in rows if r["state"] == "ready"]
    print("\n可迁移: %s" % (", ".join(usable) if usable else "无"))
    return 0


def cmd_status(args):
    out = {}
    for row in discover.probe_all():
        name = row["ime"]
        out[name] = {k: row.get(k) for k in
                     ("installed", "version", "running", "records", "state", "detail", "caps")}
        if row.get("stats"):
            out[name]["stats"] = row["stats"]
    out["usable"] = discover.usable()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_gui(args):
    from .gui import server
    return server.main(args.port)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="imefuse")
    parser.add_argument("--version", action="version", version="imefuse " + __version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="export one IME to canonical jsonl/tsv")
    p.add_argument("ime", choices=sorted(ADAPTERS))
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="merge a canonical file into one IME")
    p.add_argument("ime", choices=sorted(ADAPTERS))
    p.add_argument("file")
    p.add_argument("--strategy", default="P1", choices=sorted(merge_mod.STRATEGIES))
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--force-unsafe", action="store_true",
                   help="allow writing into an IME whose write path failed verification")
    p.add_argument("--process-marker", default="WeType.app/Contents/MacOS/WeType")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("detect", help="detect which IMEs exist on this Mac")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("gui")
    p.add_argument("--port", type=int, default=8770)
    p.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
