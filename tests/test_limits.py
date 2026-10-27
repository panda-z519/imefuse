import os
import shutil
import tempfile
import unittest

from imefuse import limits


def row(**kw):
    base = {"ime": "doubao", "state": "ready", "data_root": "", "caps": {},
            "stats": {}}
    base.update(kw)
    return base


class TestDoubaoCapacity(unittest.TestCase):
    def test_room_on_a_healthy_dict(self):
        r = row(stats={"index_entries": 2500, "record_count": 1000,
                       "data_capacity": 80000, "data_used": 40000})
        level, items = limits.assess("doubao", r)
        cap = [i for i in items if i["code"] == "doubao-capacity"][0]
        # 剩 1500 条 / 40000 字节，按 40 字节/条 -> 1000 条是瓶颈
        self.assertIn("1000", cap["text"])
        self.assertEqual(cap["level"], limits.READY)

    def test_nearly_full_is_limited(self):
        r = row(stats={"index_entries": 2500, "record_count": 2400,
                       "data_capacity": 80000, "data_used": 79000})
        level, items = limits.assess("doubao", r)
        cap = [i for i in items if i["code"] == "doubao-capacity"][0]
        self.assertEqual(cap["level"], limits.LIMITED)
        self.assertEqual(level, limits.LIMITED)

    def test_full_dict_blocks(self):
        r = row(stats={"index_entries": 2500, "record_count": 2500,
                       "data_capacity": 80000, "data_used": 80000})
        level, items = limits.assess("doubao", r)
        cap = [i for i in items if i["code"] == "doubao-capacity"][0]
        self.assertEqual(cap["level"], limits.BLOCKED)
        self.assertEqual(level, limits.BLOCKED)

    def test_bytes_can_bind_before_records(self):
        # 记录数还有 2000 个空位，但平均 152 字节/条时字节只够 ~26 条
        r = row(stats={"index_entries": 2500, "record_count": 500,
                       "data_capacity": 80000, "data_used": 76000})
        _level, items = limits.assess("doubao", r)
        cap = [i for i in items if i["code"] == "doubao-capacity"][0]
        self.assertIn("约 26 条", cap["text"])
        self.assertEqual(cap["level"], limits.LIMITED)

class TestWetypeDictVersion(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="imefuse-limits-")
        self.base = os.path.join(self.home, "WeType")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _touch(self, name):
        os.makedirs(os.path.join(self.base, "userDict", name))

    def test_v5_is_fine(self):
        self._touch("v5")
        _level, items = limits.assess("wetype", row(ime="wetype", data_root=self.base))
        self.assertEqual([i for i in items if i["code"] == "wetype-dict-version"], [])

    def test_unknown_version_blocks_with_the_dir_name(self):
        self._touch("v6")
        level, items = limits.assess("wetype", row(ime="wetype", data_root=self.base))
        hit = [i for i in items if i["code"] == "wetype-dict-version"][0]
        self.assertEqual(hit["level"], limits.BLOCKED)
        self.assertIn("v6", hit["text"])
        self.assertEqual(level, limits.BLOCKED)

    def test_v5_alongside_v6_still_works(self):
        self._touch("v5")
        self._touch("v6")
        _level, items = limits.assess("wetype", row(ime="wetype", data_root=self.base))
        self.assertEqual([i for i in items if i["code"] == "wetype-dict-version"], [])


class TestStateMapping(unittest.TestCase):
    def test_missing_ime_blocks_with_reason(self):
        level, items = limits.assess("sogou", row(ime="sogou", state="missing",
                                                  detail="本机没装这个输入法"))
        self.assertEqual(level, limits.BLOCKED)
        self.assertEqual(items[0]["code"], "ime-missing")

    def test_unknown_format_blocks(self):
        level, _items = limits.assess("wetype", row(ime="wetype", state="unknown-format"))
        self.assertEqual(level, limits.BLOCKED)

    def test_sogou_without_counter_is_limited(self):
        level, items = limits.assess("sogou", row(ime="sogou",
                                                  caps={"has_count": False}))
        self.assertEqual(level, limits.LIMITED)
        self.assertEqual(items[0]["code"], "sogou-no-counter")

    def test_doubao_add_only_is_limited(self):
        _level, items = limits.assess("doubao", row(caps={"has_count": True}))
        codes = [i["code"] for i in items]
        self.assertIn("doubao-add-only", codes)


class TestWorst(unittest.TestCase):
    def test_worst_wins(self):
        items = [limits.note("a", limits.READY, ""),
                 limits.note("b", limits.LIMITED, ""),
                 limits.note("c", limits.BLOCKED, "")]
        self.assertEqual(limits.worst(items), limits.BLOCKED)
        self.assertEqual(limits.worst([]), limits.READY)
        self.assertEqual(limits.worst(items[:2]), limits.LIMITED)


class TestFlatten(unittest.TestCase):
    def test_flatten_keeps_ime_and_code(self):
        rows = [{"ime": "doubao",
                 "limits": [limits.note("x", limits.LIMITED, "t", "d")]}]
        flat = limits.flatten(rows)
        self.assertEqual(flat, [{"ime": "doubao", "level": limits.LIMITED,
                                 "code": "x", "text": "t", "detail": "d"}])

    def test_flatten_tolerates_missing_limits(self):
        self.assertEqual(limits.flatten([{"ime": "sogou"}]), [])


if __name__ == "__main__":
    unittest.main()
