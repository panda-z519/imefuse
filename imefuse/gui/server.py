"""Local web GUI for imefuse.  Pure stdlib: ThreadingHTTPServer + static files."""
import json
import os
import re
import time
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import actions, backup, canonical, discover, paths

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EXPORT_DIR = paths.exports_dir()

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
    return actions.require_ready(ime)


def list_exports():
    return actions.list_exports()


def do_export(ime):
    return actions.export_ime(ime)


def diff_records(ime, incoming_list, strategy="P1", alpha=0.5):
    return actions.diff_records(ime, incoming_list, strategy, alpha)


def do_diff(ime, filename, strategy="P1", alpha=0.5):
    path = os.path.join(EXPORT_DIR, os.path.basename(filename))
    return actions.diff_file(ime, path, strategy, alpha)


def do_migrate(source, target, strategy="P1", alpha=0.5, apply=False):
    return actions.migrate(source, target, strategy, alpha, apply)


def apply_records(ime, incoming_list, header=None, strategy="P1", alpha=0.5,
                  force_unsafe=False):
    return actions.apply_records(ime, incoming_list, header, strategy, alpha, force_unsafe)


def do_apply(ime, filename, strategy="P1", alpha=0.5, force_unsafe=False):
    path = os.path.join(EXPORT_DIR, os.path.basename(filename))
    return actions.apply_file(ime, path, strategy, alpha, force_unsafe)


SAFE_NAME = re.compile(r"[^\w.-]+", re.UNICODE)  # keep CJK, drop path junk


def download_path(name):
    path = os.path.join(EXPORT_DIR, os.path.basename(name))
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    return path


def save_upload(filename, data):
    """Store a file the user brought from another machine."""
    name = os.path.basename(filename or "upload.jsonl")
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".jsonl", ".json", ".tsv"):
        raise ValueError("只支持 .jsonl / .json / .tsv")
    if not data:
        raise ValueError("文件是空的")
    os.makedirs(EXPORT_DIR, exist_ok=True)
    stem = SAFE_NAME.sub("-", os.path.splitext(name)[0])[:60] or "upload"
    dest = os.path.join(EXPORT_DIR, "upload-%s-%s%s"
                        % (time.strftime("%Y%m%d-%H%M%S"), stem, ext))
    with open(dest, "wb") as fh:
        fh.write(data)
    try:
        if ext == ".tsv":
            _header, recs = canonical.read_tsv(dest)
        else:
            _header, recs = canonical.read_jsonl(dest)
    except Exception as exc:  # noqa: BLE001
        os.unlink(dest)
        raise ValueError("文件解析失败：%s" % exc) from exc
    if not recs:
        os.unlink(dest)
        raise ValueError("文件里没有可导入的词条")
    bad = [r for r in recs if not r.get("w")]
    if bad:
        os.unlink(dest)
        raise ValueError("有 %d 条记录缺少词面，可能不是 imefuse 导出文件" % len(bad))
    return {"file": os.path.basename(dest), "count": len(recs),
            "source": (_header or {}).get("source", "upload")}


class Server(ThreadingHTTPServer):
    """Threading server that ignores clients hanging up mid-response."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


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
            if path.startswith("/api/download/"):
                name = path[len("/api/download/"):]
                full = download_path(name)
                with open(full, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s"' % os.path.basename(full))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if path == "/api/status":
                rows = discover.probe_all()
                return self._json({"imes": rows,
                                   "usable": discover.usable(rows),
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
            if path == "/api/upload":
                # raw bytes: do NOT go through the JSON body reader first
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0:
                    raise ValueError("没有收到文件内容")
                if n > 32 * 1024 * 1024:
                    raise ValueError("文件太大（上限 32 MB）")
                raw_name = self.headers.get("X-Filename") or "upload.jsonl"
                from urllib.parse import unquote
                return self._json(save_upload(unquote(raw_name), self.rfile.read(n)))
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


def _serving(port, timeout=0.6):
    """True when an imefuse instance already answers on this port."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port,
                                    timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _open_browser(url):
    """Opening from a GUI-launched process is unreliable -- use /usr/bin/open."""
    if os.environ.get("IMEFUSE_NO_BROWSER"):
        return True
    try:
        if subprocess.run(["/usr/bin/open", url], check=False).returncode == 0:
            return True
    except OSError:
        pass
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001
        return False


def main(port=8770, tries=20):
    # Double-clicking the app twice should focus the running window, not die
    # with "address already in use".
    for candidate in range(port, port + tries):
        if _serving(candidate):
            url = "http://127.0.0.1:%d/" % candidate
            print("imefuse 已在运行 -> %s" % url)
            _open_browser(url)
            return 0

    httpd = None
    for candidate in range(port, port + tries):
        try:
            httpd = Server(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("no free port in %d..%d" % (port, port + tries - 1))

    url = "http://127.0.0.1:%d/" % port
    print("imefuse GUI -> %s" % url)
    threading.Timer(0.6, lambda: _open_browser(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0
