import struct
import unittest

from imefuse.adapters import sogou


def make_container(records=(), slots=64, arena=4096, base=1_000_000):
    """Build a minimal but structurally valid SGPU container."""
    idx_off = 0x54
    idx_size = slots * 4
    arena_off = idx_off + idx_size
    buf = bytearray(arena_off + arena)
    buf[0:4] = sogou.MAGIC
    struct.pack_into("<I", buf, 0x04, 0x28)
    struct.pack_into("<I", buf, sogou.O_VERSION, 20160521)
    struct.pack_into("<I", buf, sogou.O_SIZE, len(buf))
    struct.pack_into("<I", buf, sogou.O_NONCE, base)
    struct.pack_into("<I", buf, sogou.O_GEN, 1)
    struct.pack_into("<I", buf, sogou.O_GENSUM, 0)
    struct.pack_into("<I", buf, sogou.O_IDX_OFF, idx_off)
    struct.pack_into("<I", buf, sogou.O_IDX_SIZE, idx_size)
    struct.pack_into("<I", buf, sogou.O_COUNT, 0)
    struct.pack_into("<I", buf, sogou.O_ARENA_OFF, arena_off)
    struct.pack_into("<I", buf, sogou.O_ARENA_SIZE, arena)
    struct.pack_into("<I", buf, sogou.O_ARENA_USED, 0)
    struct.pack_into("<I", buf, sogou.O_TAIL, 9)
    return buf


class TestSyllableTable(unittest.TestCase):
    def test_table_is_loaded(self):
        table = sogou.syllable_table()
        self.assertGreater(len(table), 400)

    def test_known_indices(self):
        table = sogou.syllable_table()
        self.assertEqual(table.index("ni"), 219)
        self.assertEqual(table.index("hao"), 117)

    def test_ids_for_word(self):
        ids = sogou.ids_for("ni hao", "你好")
        self.assertEqual(len(ids), 2)
        self.assertEqual(sogou.pinyin_for(ids), "ni hao")


class TestRecordRoundtrip(unittest.TestCase):
    def test_build_and_parse(self):
        ids = sogou.ids_for("ni hao", "你好")
        word = "你好".encode("utf-16-le")
        rec = sogou.build_record(42, ids, word)
        holder = make_container()
        arena_off = sogou.Container(bytes(holder)).arena_off
        holder[arena_off:arena_off + len(rec)] = rec
        c = sogou.Container(bytes(holder))
        gen, tag, typ, wbytes, head, trail, length = c.record_at(c.arena_off)
        self.assertEqual(gen, 42)
        self.assertEqual(wbytes.decode("utf-16-le"), "你好")
        self.assertEqual(list(head), list(ids))
        self.assertEqual(length, len(rec))


class TestNonceInvariant(unittest.TestCase):
    """O_NONCE = base + arena_used + count is what the engine validates."""

    def test_write_updates_nonce(self):
        import os
        import tempfile
        buf = make_container()
        base = struct.unpack_from("<I", buf, sogou.O_NONCE)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "usr.bin")
            with open(path, "wb") as fh:
                fh.write(bytes(buf))
            plan, count = sogou.write_entries(
                [("ni hao", "你好", 1, 0)], dry_run=False, path=path)
            with open(path, "rb") as fh:
                out = fh.read()
        self.assertEqual(plan[0]["status"], "ok")
        nonce = struct.unpack_from("<I", out, sogou.O_NONCE)[0]
        used = struct.unpack_from("<I", out, sogou.O_ARENA_USED)[0]
        cnt = struct.unpack_from("<I", out, sogou.O_COUNT)[0]
        self.assertEqual(cnt, 1)
        self.assertEqual(nonce - used - cnt, base)

    def test_write_is_idempotent(self):
        import os
        import tempfile
        buf = make_container()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "usr.bin")
            with open(path, "wb") as fh:
                fh.write(bytes(buf))
            sogou.write_entries([("ni hao", "你好", 1, 0)], dry_run=False, path=path)
            plan, _ = sogou.write_entries(
                [("ni hao", "你好", 1, 0)], dry_run=False, path=path)
        self.assertEqual(plan[0]["status"], "exists")

    def test_index_stays_sorted(self):
        import os
        import tempfile
        buf = make_container()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "usr.bin")
            with open(path, "wb") as fh:
                fh.write(bytes(buf))
            sogou.write_entries(
                [("ni hao", "你好", 1, 0), ("a", "啊", 1, 0)], dry_run=False, path=path)
            with open(path, "rb") as fh:
                c = sogou.Container(fh.read())
        keys = []
        for slot in range(c.count):
            off = struct.unpack_from("<I", c.buf, c.idx_off + slot * 4)[0]
            r = c.record_at(c.arena_off + off)
            keys.append(tuple(r[4] if r[4] else r[5]))
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
