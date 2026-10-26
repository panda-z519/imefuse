# Contributing to imefuse

Thanks for helping. The project is deliberately small: pure Python, no
dependencies, one adapter per input method.

## Ground rules

1. **Never write to a live IME store in a test.** Tests must use synthetic
   containers (see `tests/test_sogou.py`, `tests/test_doubao.py`) or a fake home
   via `IMEFUSE_HOME` / `IMEFUSE_APP_DIRS` (see `tests/test_discover.py`).
2. **Never commit personal data.** `exports/`, `backups/`, `runs/`, `state/` and
   `work/` are git-ignored on purpose — a dictionary is a fingerprint of a person.
3. **A write is only "done" when the IME still works.** If you add an adapter,
   include evidence that the application starts, the entry survives a restart,
   and the word actually appears as a candidate.

## Adding support for another IME

Create `imefuse/adapters/<name>.py` exposing four things:

```python
CAPS = {"has_count": bool, "writable": bool, "label": str, "note": str}

def export(path=None):      # -> (records, stats)
    """records: list of {w, py, n, t, raw, kind, src}"""

def write_entries(entries, dry_run=True, path=None):   # -> (plan, info)
    """entries: iterable of (pinyin, word, count, ts); add-only, never rewrite"""

def check():                # -> {store, compatible, detail}
    """validate the on-disk format before anything is written"""
```

Then register it in `imefuse/discover.py::IMES` (bundle id, app names, data
root) and add it to `imefuse/cli.py::ADAPTERS`.

`check()` matters more than it looks: it is the only thing standing between a
future IME update and a corrupted dictionary. Return `compatible=False` with a
clear `detail` whenever the bytes do not match what you reversed.

## Writing up a format

Put it in `docs/formats/<name>.md` and cover:

- where the data lives, and how you found it
- container layout (offsets, magic, version fields)
- record layout, field by field
- any encoding/obfuscation, with the exact transform
- **how you verified it** — a round-trip that reproduces the original file
  byte-for-byte is the gold standard
- what you could *not* work out

## Tests

```bash
make test     # 46 tests, no IME required
make lint     # byte-compile
```

Tests must pass on a machine with no input methods installed at all.

## Pull requests

- Keep the diff focused; one adapter or one fix per PR.
- Run `make test` and `make lint`.
- If you touched a write path, say in the description how you verified the IME
  still starts and still ranks the imported word.
