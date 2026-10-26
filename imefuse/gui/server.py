"""Local web GUI for imefuse.  Pure stdlib: ThreadingHTTPServer + static files."""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import backup, canonical, discover, merge as merge_mod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EXPORT_DIR = os.path.join(ROOT, "exports")

def get_adapter(ime):
    return discover.get_adapter(ime)


def _has_count(ime):
    try:
        return bool(getattr(get_adapter(ime), "CAPS", {}).get("has_count", True))
    except Exception:
        return True


def ime_status(ime):
    return discover.probe(ime)


def require_ready(ime):
    row = discover.probe(ime)
    if row["state"] != "ready":
        raise RuntimeError("%s 当前不可用：%s" % (row["label"], row["detail"] or row["state"]))
    return row


def list_exports():
    if not os.path.isdir(EXPORT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(EXPORT_DIR), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(EXPORT_DIR, name)
        try:
            header, recs = canonical.read_jsonl(path)
        except Exception:
            continue
        out.append({"file": name, "count": len(recs),
                    "source": (header or {}).get("source"),
                    "at": (header or {}).get("exported_at")})
    return out


def do_export(ime):
    require_ready(ime)
    mod = get_adapter(ime)
    records, stats = mod.export()
    records = canonical.dedupe(records)
    header = canonical.make_header(ime, count=len(records))
    import time as _t
    stamp = _t.strftime("%Y%m%d-%H%M%S")
    jsonl = os.path.join(EXPORT_DIR, "%s-%s.jsonl" % (ime, stamp))
    tsv = os.path.join(EXPORT_DIR, "%s-%s.tsv" % (ime, stamp))
    os.makedirs(EXPORT_DIR, exist_ok=True)
    canonical.write_jsonl(jsonl, header, records)
    canonical.write_tsv(tsv, records)
    top = sorted(records, key=lambda r: -r.get("n", 0))[:30]
    return {"file": os.path.basename(jsonl), "tsv": os.path.basename(tsv),
            "count": len(records), "stats": stats,
            "top": [{"w": r["w"], "py": r.get("py", ""), "n": r.get("n", 0)} for r in top]}


def diff_records(ime, incoming_list, strategy="P1", alpha=0.5):
    require_ready(ime)
    incoming = {canonical.merge_key(r): r for r in incoming_list}
    mod = get_adapter(ime)
    records, _stats = mod.export()
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


def do_diff(ime, filename, strategy="P1", alpha=0.5):
    path = os.path.join(EXPORT_DIR, os.path.basename(filename))
    _header, incoming_list = canonical.read_jsonl(path)
    return diff_records(ime, incoming_list, strategy, alpha)


def do_migrate(source, target, strategy="P1", alpha=0.5, apply=False):
    """One-shot: export `source`, merge the result into `target`."""
    require_ready(source)
    require_ready(target)
    if source == target:
        raise RuntimeError("源和目标不能是同一个输入法")
    mod = get_adapter(source)
    records, stats = mod.export()
    records = canonical.dedupe(records)
    if not apply:
        out = diff_records(target, records, strategy, alpha)
        out["source"] = source
        out["source_records"] = len(records)
        out["source_stats"] = stats
        out["dry_run"] = True
        return out
    header = canonical.make_header(source, count=len(records))
    out = apply_records(target, records, header, strategy, alpha)
    out["source"] = source
    out["source_records"] = len(records)
    out["dry_run"] = False
    return out


def apply_records(ime, incoming_list, header=None, strategy="P1", alpha=0.5,
                  force_unsafe=False):
    require_ready(ime)
    mod = get_adapter(ime)
    caps = getattr(mod, "CAPS", {})
    if not caps.get("writable", True) and not force_unsafe:
        raise RuntimeError("该输入法写回未通过引擎校验，已拒绝写入：" + caps.get("note", ""))
    incoming = {canonical.merge_key(r): r for r in incoming_list}
    records, _stats = mod.export()
    existing = {canonical.merge_key(r): r for r in records}
    plan, summary = merge_mod.plan_merge(existing, incoming, strategy=strategy, alpha=alpha,
                                         target_has_count=_has_count(ime))
    todo = [p for p in plan if p["action"] in ("add", "bump")]
    if not todo:
        return {"summary": summary, "written": 0, "note": "nothing to write"}
    snap = backup.snapshot(ime, label="pre-import")
    stopped = backup.quit_ime(ime)
    import time as _t
    now = int(_t.time())
    entries = [(p["key"][1], p["key"][0], p["merged"], p.get("ts") or now) for p in todo]
    try:
        plan_w, seq = mod.write_entries(entries, dry_run=False)
        ok = sum(1 for p in plan_w if p.get("status") in ("ok", "add"))
        failed = [p.get("key") or p.get("w") for p in plan_w
                  if p.get("status") not in ("ok", "add")]
        backup.start_ime(ime)
        return {"summary": summary, "written": ok, "failed": failed[:20],
                "seq": seq, "backup": snap["path"], "stopped": stopped,
                "export_id": (header or {}).get("export_id")}
    except Exception as exc:  # noqa: BLE001
        backup.start_ime(ime)
        raise


def do_apply(ime, filename, strategy="P1", alpha=0.5, force_unsafe=False):
    path = os.path.join(EXPORT_DIR, os.path.basename(filename))
    header, incoming_list = canonical.read_jsonl(path)
    return apply_records(ime, incoming_list, header, strategy, alpha, force_unsafe)


class Handler(BaseHTTPRequestHandler):
    server_version = "imefuse/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = os.path.basename(path)
                full = os.path.join(STATIC, name)
                if not os.path.exists(full):
                    return self._send(404, "not found", "text/plain")
                ctype = "text/css" if name.endswith(".css") else "application/javascript"
                if name.endswith(".png"):
                    ctype = "image/png"
                with open(full, "rb") as fh:
                    return self._send(200, fh.read(), ctype + "; charset=utf-8")
            if path.startswith("/shots/"):
                full = os.path.join(ROOT, "runs", "shots", os.path.basename(path))
                if not os.path.exists(full):
                    return self._send(404, "not found", "text/plain")
                with open(full, "rb") as fh:
                    return self._send(200, fh.read(), "image/png")
            if path == "/api/status":
                return self._json({"imes": discover.probe_all(),
                                   "usable": discover.usable(),
                                   "exports": list_exports(),
                                   "backups": backup.list_backups()[:20]})
            if path == "/api/backups":
                return self._json({"backups": backup.list_backups()})
            return self._send(404, "not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if path == "/api/export":
                return self._json(do_export(body["ime"]))
            if path == "/api/diff":
                return self._json(do_diff(body["ime"], body["file"],
                                          body.get("strategy", "P1"),
                                          float(body.get("alpha", 0.5))))
            if path == "/api/migrate":
                return self._json(do_migrate(body["source"], body["target"],
                                             body.get("strategy", "P1"),
                                             float(body.get("alpha", 0.5)),
                                             bool(body.get("apply"))))
            if path == "/api/apply":
                return self._json(do_apply(body["ime"], body["file"],
                                           body.get("strategy", "P1"),
                                           float(body.get("alpha", 0.5))))
            if path == "/api/backup":
                return self._json(backup.snapshot(body["ime"], label=body.get("label", "manual")))
            if path == "/api/restore":
                return self._json(backup.restore(body["name"], body.get("ime")))
            return self._send(404, "not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 500)


def main(port=8770):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("imefuse GUI -> %s" % url)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0
