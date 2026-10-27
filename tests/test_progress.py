"""进度显示的契约测试。

重点不是"画得好不好看"，而是三条不变量：

1. 非终端（StringIO、管道）一行都不输出，否则会污染 JSON 和测试输出；
2. 阶段计数连续，不重数、不跳号；
3. `total()` 不会被中途覆盖（migrate 传 1+6 给 apply_records 那个坑）。
"""
import io
import unittest

from imefuse import progress


class TestNullProgress(unittest.TestCase):
    def test_writes_nothing(self):
        out = io.StringIO()
        p = progress.make(stream=out)
        with p.total(3).step("a") as st:
            st.note("x")
        with p.step("b"):
            pass
        self.assertEqual(out.getvalue(), "")

    def test_is_null_for_plain_stringio(self):
        self.assertIsInstance(progress.make(stream=io.StringIO()),
                              progress.NullProgress)

    def test_null_helper(self):
        self.assertIsInstance(progress.null(), progress.NullProgress)


class TestConsoleProgress(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()

    def _console(self):
        return progress.ConsoleProgress(self.out, enabled=True)

    def test_prefix_counts_up(self):
        p = self._console().total(3)
        for label in ("a", "b", "c"):
            with p.step(label):
                pass
        text = self.out.getvalue()
        for n in ("[1/3]", "[2/3]", "[3/3]"):
            self.assertIn(n, text)

    def test_no_prefix_without_total(self):
        p = self._console()
        with p.step("single") as st:
            st.note("42 条")
        text = self.out.getvalue()
        self.assertNotIn("[1/", text)
        self.assertIn("single", text)
        self.assertIn("42 条", text)

    def test_failure_marks_the_step(self):
        p = self._console().total(1)
        with self.assertRaises(ValueError):
            with p.step("boom"):
                raise ValueError("nope")
        text = self.out.getvalue()
        self.assertIn("✗", text)
        self.assertIn("boom", text)

    def test_elapsed_time_is_reported(self):
        p = self._console()
        with p.step("t"):
            pass
        # 结束时总会带一个 "0.0s" 之类的真实耗时
        self.assertRegex(self.out.getvalue(), r"\d+\.\d+s")

    def test_disabled_console_is_silent(self):
        p = progress.ConsoleProgress(self.out, enabled=False)
        with p.total(2).step("quiet") as st:
            st.note("ignored")
        self.assertEqual(self.out.getvalue(), "")


class TestForceMode(unittest.TestCase):
    def test_force_gives_a_console_even_without_tty(self):
        out = io.StringIO()
        self.assertIsInstance(progress.make(stream=out, force=True),
                              progress.ConsoleProgress)



class TestStageCounts(unittest.TestCase):
    """阶段数和代码里真实的 total(...) 必须对得上。

    之前 migrate 传 7、apply_records 又自己 total(7)，结果打印成 [8/7]。
    这条测试直接跑一遍真实调用，检查编号连续且不越界。
    """

    def _run_with_fake_adapter(self, apply_flag):
        from unittest import mock
        from imefuse import actions, limits

        out = io.StringIO()
        p = progress.ConsoleProgress(out, enabled=True)
        row = {"ime": "doubao", "label": "D", "state": "ready",
               "level": limits.READY, "installed": True, "caps": {"has_count": True},
               "records": 1, "limits": []}
        with mock.patch.object(actions, "require_ready", lambda ime: row), \
                mock.patch.object(actions, "require_usable", lambda ime: row), \
                mock.patch.object(actions.discover, "get_adapter") as get_adapter, \
                mock.patch.object(actions.backup, "snapshot",
                                  lambda ime, label="": {"path": "/tmp/b", "size": "1K"}), \
                mock.patch.object(actions.backup, "quit_ime", lambda ime: True), \
                mock.patch.object(actions.backup, "start_ime", lambda ime: True):
            mod = mock.Mock()
            mod.CAPS = {"has_count": True, "writable": True}
            mod.export.return_value = ([], {})
            mod.write_entries.return_value = ([{"status": "ok"}], 1)
            get_adapter.return_value = mod
            actions.migrate("wetype", "doubao", apply=apply_flag, progress=p)
        return out.getvalue()

    def _numbers(self, text):
        import re
        return [tuple(int(x) for x in m) for m in
                re.findall(r"\[(\d+)/(\d+)\]", text.replace("\r", "\n"))]

    def test_migrate_apply_numbers_are_consistent(self):
        text = self._run_with_fake_adapter(apply_flag=True)
        seen = self._numbers(text)
        self.assertTrue(seen, "应当有 n/N 形式的阶段输出")
        self.assertEqual({total for _d, total in seen}, {8},
                         "migrate apply 应该是 1 读源 + 7 目标 = 8")
        for done, total in seen:
            self.assertLessEqual(done, total, "阶段计数越界: %r" % ((done, total),))

    def test_migrate_preview_is_a_single_step(self):
        text = self._run_with_fake_adapter(apply_flag=False)
        self.assertEqual({total for _d, total in self._numbers(text)}, {1})


if __name__ == "__main__":
    unittest.main()
