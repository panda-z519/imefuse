"""向导的流程测试：不碰真机词库、不真的写入。

做法是把 stdin/stdout 换成假终端，逐条喂按键序列，再检查输出与是否
调用了写入动作。探测（probe_all）和动作（export_im / migrate / ...）都用
mock.patch 顶掉 —— 验证的是**决策流程**：哪一步该问、哪一步不该写。
"""
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from imefuse import limits, wizard


class FakeTTY(io.StringIO):
    """isatty() 为真的 StringIO：让向导认为自己在终端里。"""

    def isatty(self):
        return True


def drive(keys):
    """喂一串按键，返回 (exit_code, 输出)。"""
    stdin = FakeTTY("\n".join(keys) + "\n")
    stdout = io.StringIO()
    with mock.patch.object(wizard.sys, "stdin", stdin), \
            mock.patch.object(wizard.sys, "stdout", stdout):
        code = wizard.run()
    return code, stdout.getvalue()


ROW = {
    "ime": "doubao", "label": "豆包输入法", "state": "ready",
    "level": limits.LIMITED, "installed": True, "version": "1.0.3",
    "records": 100, "detail": "", "caps": {"has_count": True},
    "limits": [limits.note("doubao-capacity", limits.LIMITED,
                           "还能再写入约 10 条", "", value=10)],
}

TWO_IMES = [
    {"ime": "wetype", "label": "微信键盘", "state": "ready",
     "level": limits.READY, "installed": True, "version": "3.0.0",
     "records": 200, "detail": "", "caps": {"has_count": True},
     "limits": []},
    ROW,
]

EMPTY_SUMMARY = {"add": 0, "bump": 0, "conflict": 0, "keep": 0,
                 "deleted": 0, "lowered": 0}


class WizardCase(unittest.TestCase):
    """每个用例都在临时数据目录里跑，并注入自己的 probe/actions。"""

    rows = [ROW]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="imefuse-wizard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"IMEFUSE_DATA": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        probe = mock.patch.object(wizard.discover, "probe_all",
                                  lambda: [dict(r) for r in self.rows])
        probe.start()
        self.addCleanup(probe.stop)

    def patch_action(self, name, fn):
        patcher = mock.patch.object(wizard.actions, name, fn)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestNonInteractive(unittest.TestCase):
    def test_refuses_without_tty(self):
        stdout = io.StringIO()
        with mock.patch.object(wizard.sys, "stdin", io.StringIO("")), \
                mock.patch.object(wizard.sys, "stdout", stdout):
            code = wizard.run()
        self.assertEqual(code, 2)
        self.assertIn("需要终端", stdout.getvalue())


class TestMenu(WizardCase):
    def test_q_exits_cleanly(self):
        code, out = drive(["q"])
        self.assertEqual(code, 0)
        self.assertIn("已退出", out)

    def test_status_shows_every_ime_and_limit(self):
        code, out = drive(["1", "n"])
        self.assertEqual(code, 0)
        self.assertIn("doubao", out)
        self.assertIn("还能再写入约 10 条", out)
        self.assertIn("可选目标", out)

    def test_doctor_lists_capability_and_detail(self):
        _code, out = drive(["7", "n"])
        self.assertIn("能力与限制", out)
        self.assertIn("doubao", out)

    def test_bad_input_is_rejected_then_accepted(self):
        _code, out = drive(["abc", "99", "q"])
        self.assertIn("请输入编号", out)
        self.assertIn("没有 99 这个编号", out)

    def test_q_at_sub_prompt_runs_nothing(self):
        self.patch_action("export_ime",
                          mock.Mock(side_effect=AssertionError("不该被调用")))
        code, out = drive(["2", "q"])
        self.assertEqual(code, 0)
        self.assertIn("已退出", out)


class TestExportFlow(WizardCase):
    def test_export_then_decline_migrate(self):
        export = mock.Mock(return_value={"count": 3, "file": "x.jsonl",
                                         "tsv": "x.tsv",
                                         "top": [{"w": "你", "py": "ni", "n": 1}]})
        self.patch_action("export_ime", export)
        code, out = drive(["2", "1", "n", "n"])
        self.assertEqual(code, 0)
        export.assert_called_once_with("doubao")
        self.assertIn("已导出 3 条", out)
        self.assertIn("顺便把它合并进另一个输入法吗", out)

    def test_uninstalled_ime_is_not_selectable(self):
        # 真正不可导出的是「未安装」：state 本身就不 ready。
        # 注意「词库满了」是 ready + blocked，仍然可以导出（只要读得动）。
        rows = [dict(ROW, state="missing", level=limits.BLOCKED, records=None,
                     limits=[limits.note("ime-missing", limits.BLOCKED,
                                         "本机没装这个输入法")])]
        with mock.patch.object(wizard.discover, "probe_all",
                               lambda: [dict(r) for r in rows]):
            export = mock.Mock(return_value={"count": 0, "file": "x", "tsv": "y",
                                             "top": []})
            self.patch_action("export_ime", export)
            _code, out = drive(["2", "1", "q"])
        self.assertIn("不可选", out)
        export.assert_not_called()

    def test_readonly_blocked_dict_can_still_be_exported(self):
        # ready + blocked（比如豆包词库满了）不该挡住导出
        rows = [dict(ROW, state="ready", level=limits.BLOCKED)]
        with mock.patch.object(wizard.discover, "probe_all",
                               lambda: [dict(r) for r in rows]):
            export = mock.Mock(return_value={"count": 1, "file": "x", "tsv": "y",
                                             "top": []})
            self.patch_action("export_ime", export)
            _code, _out = drive(["2", "1", "n", "n"])
        export.assert_called_once_with("doubao")


class TestMigrateFlow(WizardCase):
    rows = TWO_IMES
    def test_declining_confirm_never_writes(self):
        calls = []

        def fake(source, target, strategy="P1", alpha=0.5, apply=False, progress=None):
            calls.append(apply)
            return {"summary": dict(EMPTY_SUMMARY, add=2), "truncated": 0,
                    "rows": [{"w": "你", "py": "ni", "action": "add",
                              "old": 0, "new": 5, "merged": 5}]}

        self.patch_action("migrate", fake)
        code, out = drive(["3", "1", "1", "n", "n"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [False], "确认之前不该写入")
        self.assertIn("已取消，没有写入任何东西", out)

    def test_capacity_error_surfaces_without_writing(self):
        def fake(source, target, strategy="P1", alpha=0.5, apply=False, progress=None):
            if apply:
                raise RuntimeError("目标只能再写入约 10 条，这次要写 20 条")
            return {"summary": dict(EMPTY_SUMMARY, add=20), "rows": [],
                    "truncated": 0}

        self.patch_action("migrate", fake)
        code, out = drive(["3", "1", "1", "y", "n"])
        self.assertEqual(code, 0)
        self.assertIn("只能再写入约 10 条", out)
        self.assertIn("没有写入任何东西", out)

    def test_successful_write_reports_backup(self):
        def fake(source, target, strategy="P1", alpha=0.5, apply=False, progress=None):
            if not apply:
                return {"summary": dict(EMPTY_SUMMARY, add=1), "rows": [],
                        "truncated": 0}
            return {"summary": dict(EMPTY_SUMMARY, add=1), "written": 1,
                    "requested": 1, "backup": "/tmp/bk", "failed": []}

        self.patch_action("migrate", fake)
        code, out = drive(["3", "1", "1", "y", "n"])
        self.assertEqual(code, 0)
        self.assertIn("写入 1 / 1 条", out)
        self.assertIn("/tmp/bk", out)

    def test_nothing_to_write_stops_early(self):
        def fake(source, target, strategy="P1", alpha=0.5, apply=False, progress=None):
            self.assertFalse(apply)
            return {"summary": dict(EMPTY_SUMMARY), "rows": [], "truncated": 0}

        self.patch_action("migrate", fake)
        _code, out = drive(["3", "1", "1", "n"])
        self.assertIn("没有需要写入的条目", out)


class TestImportFlow(WizardCase):
    def test_empty_exports_is_explained(self):
        self.patch_action("list_exports", lambda: [])
        _code, out = drive(["4", "n"])
        self.assertIn("还没有导出文件", out)


class TestRestoreFlow(WizardCase):
    def test_no_backups_is_explained(self):
        self.patch_action("backups", lambda: [])
        _code, out = drive(["6", "n"])
        self.assertIn("还没有备份", out)


if __name__ == "__main__":
    unittest.main()
