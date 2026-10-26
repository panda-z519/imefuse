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


def _package_parent():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """Where exports / backups / runs live.

    Running from a source checkout we stay inside the repo (handy while
    developing).  Running from a built .app we use the standard macOS location
    so nothing is written inside the bundle.
    """
    override = os.environ.get("IMEFUSE_DATA")
    if override:
        return override
    parent = _package_parent()
    if os.path.exists(os.path.join(parent, "pyproject.toml")):
        return parent
    return os.path.join(home(), "Library", "Application Support", "imefuse")


def exports_dir():
    return os.path.join(data_dir(), "exports")


def backups_dir():
    return os.path.join(data_dir(), "backups")


def runs_dir():
    return os.path.join(data_dir(), "runs")


def state_dir():
    return os.path.join(data_dir(), "state")
