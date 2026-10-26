#!/bin/bash
# Build a self-contained single-file binary with PyInstaller.
#
#   pip install pyinstaller
#   tools/build_dist.sh
#
# Unlike tools/build_app.sh this bundles a Python interpreter, so the result
# runs on a Mac that has no python3 at all.  The GUI serves its static files
# from the package, so they must be included as data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if ! python3 -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller 未安装。先跑： python3 -m pip install pyinstaller" >&2
  exit 1
fi

echo "==> PyInstaller build"
python3 -m PyInstaller \
  --noconfirm --clean \
  --name imefuse \
  --onefile \
  --add-data "imefuse/gui/static:imefuse/gui/static" \
  --hidden-import imefuse.gui.server \
  --collect-submodules imefuse \
  --osx-bundle-identifier dev.imefuse.gui \
  entrypoint.py

echo "==> done: dist/imefuse"
echo "    直接运行: ./dist/imefuse gui"
