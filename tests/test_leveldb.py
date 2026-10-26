import os
import struct
import tempfile
import unittest

from imefuse.leveldb import crc32c, reader, snappy, writer


class TestCRC32C(unittest.TestCase):
    def test_known_vector(self):
        # standard CRC-32C check value
        self.assertEqual(crc32c.crc32c(b"123456789"), 0xE3069283)

    def test_mask_matches_leveldb(self):
        # LevelDB stores Mask(crc) in the log record header
        self.assertEqual(crc32c.masked(b"123456789"),
                         crc32c.mask(0xE3069283))


class TestSnappy(unittest.TestCase):
    def test_literal_only_block(self):
        payload = b"hello snappy"
        # varint length + one literal tag (len-1 << 2) + bytes
        block = bytes([len(payload)]) + bytes([(len(payload) - 1) << 2]) + payload
        self.assertEqual(snappy.decompress(block), payload)

    def test_copy_with_overlap(self):
        # "ab" then a copy of length 6 at offset 2 -> "abababab"
        block = bytes([8, 1 << 2]) + b"ab" + bytes([((6 - 4) << 2) | 1, 2])
        self.assertEqual(snappy.decompress(block), b"abababab")

    def test_empty(self):
        self.assertEqual(snappy.decompress(b""), b"")


class TestVarint(unittest.TestCase):
    def test_roundtrip(self):
        for value in (0, 1, 127, 128, 300, 1 << 20, (1 << 32) - 1):
            encoded = writer._varint(value)
            decoded, pos = reader._varint(encoded, 0)
            self.assertEqual(decoded, value)
            self.assertEqual(pos, len(encoded))


class TestWriteBatch(unittest.TestCase):
    def test_batch_layout(self):
        batch = writer.encode_batch(7, [(b"k", b"v"), (b"gone", None)])
        seq, count = struct.unpack_from("<QI", batch, 0)
        self.assertEqual(seq, 7)
        self.assertEqual(count, 2)
        self.assertEqual(batch[12], 1)          # kTypeValue
        self.assertIn(b"k", batch)
        self.assertIn(b"gone", batch)


class TestReadDb(unittest.TestCase):
    def test_reads_appended_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "000001.log")
            open(log, "wb").close()
            writer.append_log(log, writer.encode_batch(
                1, [(b"alpha", b"1"), (b"beta", b"2")]))
            kv = reader.read_db(tmp)
        self.assertEqual(kv[b"alpha"], b"1")
        self.assertEqual(kv[b"beta"], b"2")

    def test_newer_sequence_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "000001.log")
            open(log, "wb").close()
            writer.append_log(log, writer.encode_batch(1, [(b"k", b"old")]))
            writer.append_log(log, writer.encode_batch(2, [(b"k", b"new")]))
            kv = reader.read_db(tmp)
        self.assertEqual(kv[b"k"], b"new")

    def test_deletion_hides_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "000001.log")
            open(log, "wb").close()
            writer.append_log(log, writer.encode_batch(1, [(b"k", b"v")]))
            writer.append_log(log, writer.encode_batch(2, [(b"k", None)]))
            kv = reader.read_db(tmp)
        self.assertNotIn(b"k", kv)


if __name__ == "__main__":
    unittest.main()
