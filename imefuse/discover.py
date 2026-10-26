"""Detect which input methods exist on *this* Mac and whether we can handle them.

Nothing here is hard-coded to one machine: app bundles are looked up in the
usual macOS locations, the user data root is resolved through $HOME, and every
adapter is asked to open its own store and validate the on-disk format before
the GUI offers it.

`IMEFUSE_HOME` / `IMEFUSE_APP_DIRS` exist so the detection path can be tested
against a simulated Mac (fresh install, never-used IME, unknown version).
"""
import glob
import os
import plistlib
import subprocess

from . import paths

IMES = [
    {
        "ime": "wetype",
        "label": "微信键盘 WeType",
        "bundle_id": "com.tencent.inputmethod.wetype",
        "app_names": ["WeType.app"],
        "process": "WeType.app/Contents/MacOS/WeType",
        "start_app": "/Library/Input Methods/WeType.app",
        "data_root": "~/Library/Application Support/WeType",
    },
    {
        "ime": "sogou",
        "label": "搜狗输入法",
        "bundle_id": "com.sogou.inputmethod.sogou",
        "app_names": ["SogouInput.app"],
        "process": "SogouInput.app/Contents/MacOS/SogouInput",
        "start_app": "/Library/Input Methods/SogouInput.app",
        "data_root": "~/Library/Application Support/Sogou/InputMethod",
    },
    {
        "ime": "doubao",
        "label": "豆包输入法",
        "bundle_id": "com.bytedance.inputmethod.doubaoime",
        "app_names": ["DoubaoIme.app"],
        "process": "DoubaoIme.app/Contents/MacOS/DoubaoIme",
        "start_app": "/Library/Input Methods/DoubaoIme.app",
        "data_root": "~/Library/Application Support/DoubaoIme",
    },
]

BY_NAME = {m["ime"]: m for m in IMES}


def home():
    return paths.home()


def expand(path):
    return paths.expand(path)


def app_dirs():
    env = os.environ.get("IMEFUSE_APP_DIRS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [
        "/Library/Input Methods",
        os.path.join(home(), "Library/Input Methods"),
        "/Applications",
    ]


def find_app(ime):
    """Absolute path of the installed .app bundle, or None."""
    meta = BY_NAME[ime]
    for base in app_dirs():
        for name in meta["app_names"]:
            cand = os.path.join(base, name)
            if not os.path.isdir(cand):
                continue
            bid = read_bundle_id(cand)
            # accept when the bundle id matches, or when the bundle carries no
            # id at all (some repackaged builds) but the name matches exactly
            if bid is None or bid == meta["bundle_id"]:
                return cand
    return None


def read_bundle_id(app_path):
    plist = os.path.join(app_path, "Contents", "Info.plist")
    try:
        with open(plist, "rb") as fh:
            return plistlib.load(fh).get("CFBundleIdentifier")
    except Exception:  # noqa: BLE001
        return None


def read_version(app_path):
    if not app_path:
        return None
    plist = os.path.join(app_path, "Contents", "Info.plist")
    try:
        with open(plist, "rb") as fh:
            info = plistlib.load(fh)
        return info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
    except Exception:  # noqa: BLE001
        return None


def is_running(ime):
    marker = BY_NAME[ime]["process"]
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return bool(out.stdout.strip())


def data_root(ime):
    return expand(BY_NAME[ime]["data_root"])


def data_root_exists(ime):
    return os.path.isdir(data_root(ime))


def get_adapter(ime):
    if ime == "wetype":
        from .adapters import wetype as mod
        return mod
    if ime == "sogou":
        from .adapters import sogou as mod
        return mod
    if ime == "doubao":
        from .adapters import doubao as mod
        return mod
    raise KeyError(ime)


def probe(ime):
    """Full status for one IME: installed? data present? format understood?"""
    meta = BY_NAME[ime]
    app = find_app(ime)
    out = {
        "ime": ime,
        "label": meta["label"],
        "bundle_id": meta["bundle_id"],
        "app_path": app,
        "installed": app is not None,
        "version": read_version(app),
        "data_root": data_root(ime),
        "data_present": data_root_exists(ime),
        "running": is_running(ime) if app else False,
        "records": None,
        "store": None,
        "caps": {},
        "compatible": False,
        "state": "missing",
        "detail": "",
    }
    if not out["installed"]:
        out["state"] = "missing"
        out["detail"] = "本机没装这个输入法"
        return out
    if not out["data_present"]:
        out["state"] = "no-data"
        out["detail"] = "已安装但还没有用户数据（先打几个字让它生成词库）"
        return out
    try:
        mod = get_adapter(ime)
        out["caps"] = dict(getattr(mod, "CAPS", {}))
        out["compatible"] = True
        check = getattr(mod, "check", None)
        if callable(check):
            info = check()
            out.update({k: v for k, v in info.items()
                        if k in ("store", "compatible", "detail")})
        records, stats = mod.export()
        out["records"] = len(records)
        out["stats"] = stats
        out["state"] = "ready" if out["compatible"] else "unknown-format"
        if not out["compatible"] and not out["detail"]:
            out["detail"] = "词库格式与已逆向的版本不一致，已拒绝写入"
    except FileNotFoundError as exc:
        out["state"] = "no-data"
        out["detail"] = "找不到词库文件：%s" % exc
    except Exception as exc:  # noqa: BLE001
        out["state"] = "error"
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def probe_all():
    return [probe(m["ime"]) for m in IMES]


def usable():
    return [p["ime"] for p in probe_all() if p["state"] == "ready"]


def start_app(ime):
    app = find_app(ime) or BY_NAME[ime]["start_app"]
    subprocess.run(["open", "-a", app], capture_output=True)
