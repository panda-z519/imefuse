"""Single place that resolves the user's home, so every IME root honours the
same override.  IMEFUSE_HOME lets the whole tool run against a simulated Mac
(fresh install / never-used IME / unknown format) without touching real data."""
import os


def home():
    return os.environ.get("IMEFUSE_HOME") or os.path.expanduser("~")


def expand(path):
    if path.startswith("~"):
        return os.path.join(home(), path[2:])
    return path
