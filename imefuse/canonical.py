"""Canonical intermediate format `imefuse.dict v1`.

One JSON object per line. First line is a header:

    {"imefuse":1,"exported_at":"...","source":"wetype","ime_version":"2.2.0","export_id":"<uuid>"}

Then one record per line:

    {"w":"还是","py":"hai shi","n":37,"raw":37,"t":1761000000,"kind":"user","src":"wetype"}
"""
import json
import time
import uuid

FORMAT_VERSION = 1


def make_header(source, ime_version="", count=0):
    return {
        "imefuse": FORMAT_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "ime_version": ime_version,
        "export_id": uuid.uuid4().hex,
        "count": count,
    }


def write_jsonl(path, header, records):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")


def read_jsonl(path):
    header = None
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            # accept the pre-rename key so exports made before 0.1.0 still load
            if header is None and ("imefuse" in obj or "imefreq" in obj):
                header = obj
                continue
            records.append(obj)
    return header, records


def write_tsv(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("word\tpinyin\tcount\tlast_used\tsource\n")
        for r in sorted(records, key=lambda x: -x.get("n", 0)):
            fh.write("%s\t%s\t%s\t%s\t%s\n" % (
                r.get("w", ""), r.get("py", ""), r.get("n", 0),
                r.get("t", 0), r.get("src", "")))


def merge_key(rec):
    return (rec.get("w", ""), (rec.get("py") or "").replace(",", " ").strip())


def dedupe(records):
    """Collapse duplicates inside one export, keeping the highest count."""
    out = {}
    for r in records:
        k = merge_key(r)
        if k not in out or r.get("n", 0) > out[k].get("n", 0):
            out[k] = r
    return list(out.values())
