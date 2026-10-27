#!/bin/bash
# imefuse —— 本地词库迁移工具的唯一入口。
#
#   ./imefuse.sh doctor            # 先看这台机器能不能用、每家的硬限制
#   ./imefuse.sh detect            # 装了哪些输入法
#   ./imefuse.sh gui               # 起本地界面（默认 8770，开了就打开浏览器）
#   ./imefuse.sh export wetype
#   ./imefuse.sh import doubao file.jsonl --apply
#
# 只依赖系统自带的 bash 3.2 与 python3，不编译任何东西，也不往仓库里写文件。
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROG="imefuse.sh"

PORT=8770
DATA=""
HOME_OVERRIDE=""
PY=""
OPEN_BROWSER=1
REST=()

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
imefuse —— 把 macOS 输入法里的个人词频拿出来、合并进另一家。

用法:
  ./imefuse.sh                              # 直接进交互向导（推荐）
  ./imefuse.sh [选项] <命令> [命令参数...]

不带任何命令时会进入向导：先选要做什么（看状态 / 导出 / 迁移 / 导入 /
备份 / 回滚），再选是哪个输入法、哪份文件，最后确认才写入。

命令:
  wizard                交互向导，与不带命令相同
  doctor                每家的能力分级与已知限制（只读，建议先跑）
  detect                本机装了哪些输入法
  status                机器可读的完整状态（JSON）
  gui                   起本地界面，默认端口 8770
  export <ime>          导出到 exports/
  import <ime> <文件>   合并导入（默认只看 diff，加 --apply 才写）
  version               版本号
  help                  这段文字

通用选项（写在命令前面）:
  --port <n>            gui 的端口，默认 8770；被占用时自动往后找
  --data <目录>         导出/备份/状态放在哪，默认仓库根目录
                        （或 ~/Library/Application Support/imefuse）
  --home <目录>         把 $HOME 指到别处，用于在仿真数据上试跑
  --python <路径>       指定解释器，默认自动找 >= 3.9 的那个
  --no-browser          gui 起来后不自动打开浏览器
  -h, --help            这段文字

例子:
  ./imefuse.sh                                        # 向导
  ./imefuse.sh doctor -v
  ./imefuse.sh --port 9000 gui
  ./imefuse.sh export wetype
  ./imefuse.sh import doubao exports/wetype-20261027-110000.jsonl --apply
  ./imefuse.sh --home /tmp/sim --data /tmp/simdata doctor

写入类命令会先整目录快照、退出输入法、写入后重启；被判定为「不可用」的
输入法不会被写入。
USAGE
}

# --- 解释器 -----------------------------------------------------------------
# /usr/bin/python3 在没有装 Command Line Tools 的机器上是个失败的空壳
# （xcrun 的 shim），所以不能只看「文件存在且可执行」，必须真跑一次。
py_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    >/dev/null 2>&1
}

find_python() {
  local cand
  for cand in \
    "${IMEFUSE_PYTHON:-}" \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    "$(command -v python3 2>/dev/null)" \
    /usr/bin/python3
  do
    [ -n "$cand" ] || continue
    [ -x "$cand" ] || continue
    if py_ok "$cand"; then
      PY="$cand"
      return 0
    fi
  done
  return 1
}


# --- 参数 -------------------------------------------------------------------
# 顺序规则：命令之前的参数是 imefuse.sh 自己的选项，命令之后一律原样透传
# （doctor -v、detect --json、import doubao FILE --apply 都属于后者）。
SEEN_CMD=0
while [ $# -gt 0 ]; do
  if [ "$SEEN_CMD" = "1" ]; then
    # 命令之后的参数原样透传：doctor -v、detect --json、import X Y --apply
    REST+=("$1"); shift; continue
  fi
  case "$1" in
    --port)       [ $# -ge 2 ] || die "--port 需要一个数字"; PORT="$2"; shift 2 ;;
    --port=*)     PORT="${1#*=}"; shift ;;
    --data)       [ $# -ge 2 ] || die "--data 需要一个目录"; DATA="$2"; shift 2 ;;
    --data=*)     DATA="${1#*=}"; shift ;;
    --home)       [ $# -ge 2 ] || die "--home 需要一个目录"; HOME_OVERRIDE="$2"; shift 2 ;;
    --home=*)     HOME_OVERRIDE="${1#*=}"; shift ;;
    --python)     [ $# -ge 2 ] || die "--python 需要一个路径"; PY="$2"; shift 2 ;;
    --python=*)   PY="${1#*=}"; shift ;;
    --no-browser) OPEN_BROWSER=0; shift ;;
    -h|--help)    usage; exit 0 ;;
    --)           shift; REST+=("$@"); break ;;
    -*)           die "不认识的选项: $1（--help 看用法）" ;;
    *)            REST+=("$1"); SEEN_CMD=1; shift ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) die "--port 必须是数字: $PORT" ;;
esac

CMD="${REST[0]:-wizard}"
case "$CMD" in
  help)    usage; exit 0 ;;
  version) ;;
esac

# --- 环境 -------------------------------------------------------------------
export PYTHONPATH="${SELF_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1
[ -n "$DATA" ] && export IMEFUSE_DATA="$DATA"
[ -n "$HOME_OVERRIDE" ] && export IMEFUSE_HOME="$HOME_OVERRIDE"
[ "$OPEN_BROWSER" = "0" ] && export IMEFUSE_NO_BROWSER=1

if [ -z "$PY" ]; then
  if ! find_python; then
    cat >&2 <<'NO_PY'
imefuse: 没找到可用的 python3（需要 3.9 或更新）。

  系统自带的 python3 只是个占位符：没装 Command Line Tools 时它一启动就
  报错退出，所以不能拿它凑数。任选一种装个真解释器：

    xcode-select --install          # 装 Command Line Tools（最省事）
    brew install python             # 或者用 Homebrew

  装好后重跑 ./imefuse.sh doctor 确认。若已装好但没被找到，
  用 --python /path/to/python3 手动指定。
NO_PY
    exit 2
  fi
fi

# --- 分发 -------------------------------------------------------------------
case "$CMD" in
  version)
    exec "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from imefuse.cli import __version__; print("imefuse " + __version__)' "$SELF_DIR"
    ;;
  gui)
    exec "$PY" -m imefuse.cli gui --port "$PORT"
    ;;
  wizard)
    exec "$PY" -m imefuse.cli wizard
    ;;
  export|import|detect|status|doctor)
    exec "$PY" -m imefuse.cli "${REST[@]}"
    ;;
  *)
    usage >&2
    die "不认识的命令: $CMD"
    ;;
esac
