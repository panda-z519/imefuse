import unittest

from imefuse.adapters import doubao

# field order follows doubao.Header._FIELDS
EMPTY_HEADER = doubao.Header(
    12001, 20230630, 64, 90204, 64, 140, 204, 10000,
    2500, 0, 10204, 80000, 0, 2, 2, 16,
)


class TestCrypto(unittest.TestCase):
    def test_byte_roundtrip(self):
        for value in range(256):
            self.assertEqual(doubao.decrypt_byte(doubao.encrypt_byte(value)), value)

    def test_u16_roundtrip(self):
        for value in (0, 1, 0x41, 0x4F60, 0xFFFF, 0x1234):
            self.assertEqual(doubao.decrypt_u16(doubao.encrypt_u16(value)), value)

    def test_string_roundtrip(self):
        self.assertEqual(doubao.decrypt_str(doubao.encrypt_str("nihao")), "nihao")

    def test_utf16_roundtrip(self):
        word = "你好世界"
        self.assertEqual(doubao.decrypt_u16str(doubao.encrypt_u16str(word)), word)

    def test_ciphertext_is_not_plaintext(self):
        self.assertNotEqual(doubao.encrypt_str("nihao"), b"nihao")


class TestContainer(unittest.TestCase):
    def test_empty_image_geometry(self):
        img, h = doubao.build_image(EMPTY_HEADER, [])
        self.assertEqual(len(img), 90204)
        self.assertEqual(h.record_count, 0)
        h2, ext, recs, idx = doubao.parse(img)
        self.assertEqual(h2.dict_id, 12001)
        self.assertEqual(len(recs), 0)

    def test_record_roundtrip_is_byte_identical(self):
        rec = doubao._make_record("ni hao", "你好", 5, 1700000000, 1)
        img, _ = doubao.build_image(EMPTY_HEADER, [rec])
        h, ext, recs, idx = doubao.parse(img)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].decode(), ("nihao", "你好"))
        again, _ = doubao.build_image(h, recs)
        self.assertEqual(again, img, "re-serialising must be byte identical")

    def test_write_entries_keeps_records_sorted(self):
        import os
        import tempfile
        img, _ = doubao.build_image(EMPTY_HEADER, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "usr.dat")
            with open(path, "wb") as fh:
                fh.write(img)
            plan, info = doubao.write_entries(
                [("ni hao", "你好", 1, 0), ("a", "啊", 1, 0), ("shi jie", "世界", 1, 0)],
                dry_run=False, path=path, backup=False)
            with open(path, "rb") as fh:
                out = fh.read()
        self.assertEqual(len(plan), 3)
        self.assertTrue(all(p["status"] == "add" for p in plan))
        _h, _ext, parsed, _idx = doubao.parse(out)
        self.assertEqual(len(parsed), 3)
        for a, b in zip(parsed, parsed[1:]):
            self.assertLessEqual(doubao.key_value_compare(a, b), 0,
                                 "records must stay ordered by (key, value)")

    def test_write_is_idempotent(self):
        import os
        import tempfile
        img, _ = doubao.build_image(EMPTY_HEADER, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "usr.dat")
            with open(path, "wb") as fh:
                fh.write(img)
            doubao.write_entries([("ni hao", "你好", 1, 0)], dry_run=False,
                                 path=path, backup=False)
            plan, _ = doubao.write_entries([("ni hao", "你好", 1, 0)], dry_run=False,
                                           path=path, backup=False)
        self.assertEqual(plan[0]["status"], "exists")

    def test_capacity_guard(self):
        recs = [doubao._make_record("a", "啊", 1, 0, i + 1) for i in range(3)]
        big = doubao.Header(*[getattr(EMPTY_HEADER, k) for k in doubao.Header._FIELDS])
        big.data_capacity = 8
        big.file_size = big.data_offset + 8
        with self.assertRaises(ValueError):
            doubao.build_image(big, recs)

    def test_unknown_syllable_is_rejected(self):
        with self.assertRaises(ValueError):
            doubao._make_record("zzz", "字", 1, 0, 1)


if __name__ == "__main__":
    unittest.main()
