"""Snapshot / restore the user-data directory of each IME."""
import os
import subprocess
import time

from . import discover, paths, util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = paths.backups_dir()

def root_of(ime):
    return discover.data_root(ime)


def is_running(ime):
    return discover.is_running(ime)


def quit_ime(ime, timeout=8):
    subprocess.run(["pkill", "-TERM", "-f", discover.BY_NAME[ime]["process"]],
                   capture_output=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(ime):
            return True
        time.sleep(0.3)
    subprocess.run(["pkill", "-KILL", "-f", discover.BY_NAME[ime]["process"]],
                   capture_output=True)
    time.sleep(0.5)
    return not is_running(ime)


def start_ime(ime):
    discover.start_app(ime)
    time.sleep(2.0)
    return is_running(ime)


def snapshot(ime, label="auto"):
    src = root_of(ime)
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "%s-%s-%s" % (stamp, ime, label))
    os.makedirs(dest, exist_ok=True)
    subprocess.run(["rsync", "-a", src + "/", dest + "/"], check=True)
    size = subprocess.run(["du", "-sh", dest], capture_output=True, text=True).stdout.split()[0]
    meta = {"ime": ime, "label": label, "path": os.path.relpath(dest, ROOT),
            "at": stamp, "size": size}
    util.write_json(os.path.join(dest, ".imefuse-backup.json"), meta, indent=2)
    return meta


META_NAMES = (".imefuse-backup.json", ".imefreq-backup.json")


def _meta_path(dirpath):
    for name in META_NAMES:
        candidate = os.path.join(dirpath, name)
        if os.path.exists(candidate):
            return candidate
    return None


def list_backups():
    out = []
    if not os.path.isdir(BACKUP_DIR):
        return out
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(path):
            continue
        meta = {"name": name, "path": os.path.relpath(path, ROOT)}
        meta_path = _meta_path(path)
        if meta_path:
            meta.update(util.read_json(meta_path))
        else:
            meta["size"] = subprocess.run(["du", "-sh", path], capture_output=True,
                                          text=True).stdout.split()[0]
        out.append(meta)
    return out


def restore(name, ime=None):
    """Copy a backup back over the live data root. IME must be stopped first."""
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    meta_path = _meta_path(path)
    if ime is None:
        ime = util.read_json(meta_path)["ime"] if meta_path else name.split("-")[1]
    if is_running(ime):
        quit_ime(ime)
    dest = root_of(ime)
    os.makedirs(dest, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", path + "/", dest + "/"], check=True)
    start_ime(ime)
    return {"ime": ime, "restored_from": name}
