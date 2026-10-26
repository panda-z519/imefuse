import unittest

from imefuse import merge


def rec(word, py, n, **kw):
    out = {"w": word, "py": py, "n": n}
    out.update(kw)
    return out


class TestMergeStrategies(unittest.TestCase):
    def setUp(self):
        self.existing = {("你", "ni"): rec("你", "ni", 10)}
        self.incoming = {("你", "ni"): rec("你", "ni", 4)}

    def _merged(self, strategy):
        plan, _ = merge.plan_merge(self.existing, self.incoming, strategy=strategy)
        return next(p for p in plan if p["key"] == ("你", "ni"))

    def test_never_lowers(self):
        for strategy in ("P1", "P2", "P3", "P4"):
            row = self._merged(strategy)
            self.assertGreaterEqual(row["merged"], row["old"], strategy)

    def test_p2_is_plain_sum(self):
        self.assertEqual(self._merged("P2")["merged"], 14)

    def test_p3_is_max(self):
        self.assertEqual(self._merged("P3")["merged"], 10)

    def test_identical_counts_are_kept(self):
        plan, _ = merge.plan_merge(
            {("你", "ni"): rec("你", "ni", 5)},
            {("你", "ni"): rec("你", "ni", 5)},
        )
        row = next(p for p in plan if p["key"] == ("你", "ni"))
        self.assertEqual(row["action"], "keep")
        self.assertEqual(row["merged"], 5)

    def test_scale_matched_sum(self):
        # median existing 10, median incoming 4 -> factor 2.5 -> +10
        self.assertEqual(self._merged("P1")["merged"], 20)


class TestPlanSafety(unittest.TestCase):
    def test_nothing_is_ever_deleted(self):
        existing = {("你", "ni"): rec("你", "ni", 10), ("好", "hao"): rec("好", "hao", 3)}
        incoming = {("新", "xin"): rec("新", "xin", 1)}
        plan, summary = merge.plan_merge(existing, incoming)
        self.assertEqual(summary["deleted"], 0)
        self.assertEqual(summary["lowered"], 0)
        untouched = {p["key"] for p in plan if p["action"] == "keep"}
        self.assertIn(("好", "hao"), untouched)

    def test_target_without_counters_only_adds(self):
        existing = {("你", "ni"): rec("你", "ni", 10)}
        incoming = {("你", "ni"): rec("你", "ni", 99),
                    ("新", "xin"): rec("新", "xin", 5)}
        plan, summary = merge.plan_merge(existing, incoming, target_has_count=False)
        self.assertEqual(summary["bump"], 0)
        self.assertEqual(summary["add"], 1)
        row = next(p for p in plan if p["key"] == ("你", "ni"))
        self.assertEqual(row["action"], "keep")
        self.assertEqual(row["merged"], 10)

    def test_unknown_source_counts_are_weight_one(self):
        existing = {("你", "ni"): rec("你", "ni", 10)}
        incoming = {("你", "ni"): rec("你", "ni", 5000, count_known=False)}
        plan, _ = merge.plan_merge(existing, incoming, strategy="P2")
        row = next(p for p in plan if p["key"] == ("你", "ni"))
        self.assertEqual(row["merged"], 11)

    def test_same_word_different_pinyin_is_flagged(self):
        existing = {("行", "xing"): rec("行", "xing", 3)}
        incoming = {("行", "hang"): rec("行", "hang", 1)}
        plan, summary = merge.plan_merge(existing, incoming)
        self.assertEqual(summary["conflict"], 1)
        self.assertEqual(summary["add"], 0)


if __name__ == "__main__":
    unittest.main()
