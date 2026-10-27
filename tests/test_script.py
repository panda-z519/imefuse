"""imefuse.sh 的契约测试：选项的归属位置。

真出过一次的 bug：`doctor -v` 被当成脚本自己的选项，命令行里那个 -v 直接报
「不认识的选项」，而 README 和 Makefile 里到处写着这种用法。这里把规则钉死：
命令之前是脚本的选项，命令之后原样透传给 CLI。
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "imefuse.sh")


def run(*args):
    """在隔离的仿真 home 上跑脚本。

    每条用例都注入 --home / --data 指向临时目录：既不读真机词库（快且不依赖
    本机装了什么），也保证不会写到任何真实位置。
    """
    tmp = tempfile.mkdtemp(prefix="imefuse-script-")
    env = dict(os.environ)
    for key in ("IMEFUSE_HOME", "IMEFUSE_DATA", "IMEFUSE_PYTHON"):
        env.pop(key, None)
    try:
        return subprocess.run([SCRIPT, "--home", tmp, "--data", tmp] + list(args),
                              cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=120)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TestScriptExists(unittest.TestCase):
    def test_is_executable(self):
        self.assertTrue(os.path.isfile(SCRIPT))
        self.assertTrue(os.access(SCRIPT, os.X_OK), "imefuse.sh 必须可执行")

    def test_bash_syntax(self):
        out = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class TestCommandArgumentsPassThrough(unittest.TestCase):
    """命令之后的参数必须透传，不能被脚本自己吃掉。"""

    def test_doctor_accepts_v_after_command(self):
        out = run("doctor", "-v")
        self.assertNotIn("不认识的选项", out.stderr,
                         "doctor -v 的 -v 属于 CLI，不该被脚本拦下")
        self.assertNotIn("不认识的选项", out.stdout)
        self.assertIn("imefuse", out.stdout)

    def test_detect_accepts_json_after_command(self):
        out = run("detect", "--json")
        self.assertNotIn("不认识的选项", out.stderr)
        self.assertTrue(out.stdout.lstrip().startswith("["), out.stdout[:80])

    def test_import_accepts_apply_after_command(self):
        # 不真的写：故意给不存在的文件，CLI 会在读文件时报错；
        # 只要参数没被脚本拦截，就不会出现「不认识的选项」
        out = run("import", "doubao", "/nonexistent/imefuse-test.jsonl", "--apply")
        self.assertNotIn("不认识的选项", out.stderr)
        self.assertNotIn("不认识的选项", out.stdout)


class TestScriptOptionsBeforeCommand(unittest.TestCase):
    def test_version(self):
        out = run("version")
        self.assertEqual(out.returncode, 0)
        self.assertIn("imefuse", out.stdout)

    def test_unknown_option_still_dies(self):
        out = run("--bogus", "doctor")
        self.assertEqual(out.returncode, 1)
        self.assertIn("不认识的选项", out.stderr)

    def test_non_numeric_port_is_rejected(self):
        out = run("--port", "abc", "gui")
        self.assertEqual(out.returncode, 1)
        self.assertIn("必须是数字", out.stderr)

    def test_help(self):
        out = run("--help")
        self.assertEqual(out.returncode, 0)
        self.assertIn("doctor", out.stdout)
        self.assertIn("--port", out.stdout)


    def test_equals_form(self):
        out = run("--port=9999", "version")
        self.assertEqual(out.returncode, 0)

    def test_no_arguments_enters_wizard(self):
        # 无参数 = 想要向导。测试里 stdin 不是终端，所以应当明确退出 2
        # 并提示改用子命令 —— 绝不能读到 EOF 就当成默认选项去执行。
        out = run()
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("需要终端", out.stdout + out.stderr)
        self.assertIn("--help", out.stdout + out.stderr)

    def test_wizard_subcommand_also_refuses_without_tty(self):
        out = run("wizard")
        self.assertEqual(out.returncode, 2)
        self.assertIn("需要终端", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
