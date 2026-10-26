"""Small file helpers so every read/write closes its handle."""
import json
import os


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def read_text(path, encoding="utf-8"):
    with open(path, "r", encoding=encoding) as fh:
        return fh.read()


def write_text(path, text, encoding="utf-8"):
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj, **kwargs):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, **kwargs)


def exists(path):
    return os.path.exists(path)
