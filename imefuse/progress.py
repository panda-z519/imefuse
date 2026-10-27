"""进度显示：只报告真实发生的事情。

两条硬规则，为了不变成那种"永远卡在 90%"的假进度条：

1. **有明确阶段的地方才给 n/N。** 写入是「备份 → 退出输入法 → 写入 → 重启」
   这样的离散阶段，用户能看到真的在往前走。
2. **只有一个阻塞调用的地方只给耗时。** `adapter.export()` 是一次调用，中间
   没有任何可观测的边界，这时画百分比就是编的 —— 所以只转圈加秒数。

非终端输出（管道、CI、测试里的 StringIO）会自动退化成 NullProgress，一行
都不打印，免得把 JSON 或测试输出弄脏。
"""
import sys
import threading
import time

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TICK = 0.12


class NullProgress(object):
    """什么都不做。默认值，也是非终端环境的实现。"""

    total_count = 0

    def total(self, count):
        return self

    def step(self, label):
        return _NullStep()


class _NullStep(object):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def note(self, text):
        pass


class ConsoleProgress(object):
    """往终端里原地刷新一行。"""

    def __init__(self, stream, enabled=True):
        self.out = stream
        self.enabled = enabled
        self.count = 0
        self.total_count = 0
        self._lock = threading.Lock()

    # -- 渲染 ----------------------------------------------------------
    def _write(self, text):
        if not self.enabled:
            return
        with self._lock:
            self.out.write(text)
            self.out.flush()

    def _clear_line(self):
        """回车 + 擦到行尾：原地刷新，不留残影。"""
        self._write("\r\033[K")

    def prefix(self):
        if self.total_count:
            return "[%d/%d] " % (self.count, self.total_count)
        return ""

    # -- 对外 ----------------------------------------------------------
    def total(self, count):
        self.total_count = int(count)
        self.count = 0
        return self

    def step(self, label):
        self.count += 1
        return _ConsoleStep(self, label)


class _ConsoleStep(object):
    """一个阶段：开始时转圈计时，结束时打勾并给出真实耗时。"""

    def __init__(self, progress, label):
        self.p = progress
        self.label = label
        self.started = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._extra = ""

    def note(self, text):
        self._extra = text

    def _render(self, elapsed, final=False):
        frame = "✓" if final else FRAMES[int(elapsed / TICK) % len(FRAMES)]
        extra = (" · " + self._extra) if self._extra else ""
        self.p._clear_line()
        self.p._write("%s%s %s%s  %.1fs"
                      % (self.p.prefix(), frame, self.label, extra, elapsed))

    def _spin(self):
        while not self._stop.wait(TICK):
            self._render(time.monotonic() - self.started)

    def __enter__(self):
        self.started = time.monotonic()
        if self.p.enabled:
            self._render(0.0)
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        elapsed = time.monotonic() - self.started
        if exc_type is not None:
            self.p._clear_line()
            self.p._write("%s✗ %s  %.1fs\n" % (self.p.prefix(), self.label, elapsed))
        else:
            self._render(elapsed, final=True)
            self.p._write("\n")
        return False


def make(stream=None, force=False):
    """终端给真进度条，其余情况给 NullProgress。

    stream 默认取 stderr —— 子命令的 stdout 要留给人或 JSON，进度不能混进去。
    """
    stream = sys.stderr if stream is None else stream
    if not force:
        try:
            if not stream.isatty():
                return NullProgress()
        except (AttributeError, ValueError):
            return NullProgress()
    return ConsoleProgress(stream, enabled=True)


def null():
    return NullProgress()
