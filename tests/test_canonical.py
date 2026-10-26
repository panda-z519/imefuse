import os
import tempfile
import unittest

from imefuse import canonical


class TestCanonical(unittest.TestCase):
    def test_roundtrip(self):
        recs = [
            {"w": "你好", "py": "ni hao", "n": 7, "t": 1700000000, "src": "wetype"},
            {"w": "世界", "py": "shi jie", "n": 3, "t": 1700000001, "src": "wetype"},
        ]
        header = canonical.make_header("wetype", "1.0", count=len(recs))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.jsonl")
            canonical.write_jsonl(path, header, recs)
            back_header, back = canonical.read_jsonl(path)
        self.assertEqual(back_header["source"], "wetype")
        self.assertEqual(back_header["count"], 2)
        self.assertEqual([r["w"] for r in back], ["你好", "世界"])
        self.assertEqual(back[0]["n"], 7)

    def test_merge_key_normalises_pinyin_separators(self):
        a = canonical.merge_key({"w": "你好", "py": "ni,hao"})
        b = canonical.merge_key({"w": "你好", "py": "ni hao"})
        self.assertEqual(a, b)

    def test_dedupe_keeps_highest_count(self):
        recs = [
            {"w": "你", "py": "ni", "n": 2},
            {"w": "你", "py": "ni", "n": 9},
        ]
        out = canonical.dedupe(recs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["n"], 9)

    def test_tsv_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.tsv")
            canonical.write_tsv(path, [{"w": "你", "py": "ni", "n": 2, "t": 0, "src": "x"}])
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().strip().split("\n")
        self.assertEqual(lines[0].split("\t")[0], "word")
        self.assertIn("你", lines[1])


if __name__ == "__main__":
    unittest.main()
