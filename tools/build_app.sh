#!/bin/bash
# Build a double-clickable macOS app bundle around the Python package.
#
#   tools/build_app.sh            -> build/imefuse.app  (uses system python3)
#
# The bundle is intentionally thin: it ships the pure-python package plus a
# launcher, and runs it with whatever python3 the machine already has.  No
# PyInstaller, no network, no code signing required to run locally.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${ROOT}/build/imefuse.app"
VERSION="$(python3 - "${ROOT}/pyproject.toml" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(m.group(1) if m else "0.0.0")
PY
)"

echo "==> building imefuse.app ${VERSION}"
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"

# --- package payload -------------------------------------------------------
cp -R "${ROOT}/imefuse" "${APP}/Contents/Resources/imefuse"
find "${APP}/Contents/Resources/imefuse" -name '__pycache__' -type d -prune -exec rm -rf {} +

# --- icon ------------------------------------------------------------------
ICNS="${ROOT}/tools/AppIcon.icns"
if [ ! -f "${ICNS}" ]; then
  echo "==> generating icon (one-off)"
  TMPI="$(mktemp -d)"
  if swiftc -O "${ROOT}/tools/make_icon.swift" -o "${TMPI}/make_icon" 2>/dev/null \
     && "${TMPI}/make_icon" "${TMPI}/AppIcon.iconset" >/dev/null \
     && iconutil -c icns "${TMPI}/AppIcon.iconset" -o "${TMPI}/AppIcon.icns"; then
    cp "${TMPI}/AppIcon.icns" "${ICNS}"
  else
    echo "    (icon generation skipped -- swiftc/iconutil unavailable)"
  fi
  rm -rf "${TMPI}"
fi
if [ -f "${ICNS}" ]; then
  cp "${ICNS}" "${APP}/Contents/Resources/AppIcon.icns"
fi

# --- Info.plist ------------------------------------------------------------
cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>imefuse</string>
  <key>CFBundleDisplayName</key><string>imefuse</string>
  <key>CFBundleIdentifier</key><string>dev.imefuse.gui</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>imefuse</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
</dict>
</plist>
PLIST

# --- launcher --------------------------------------------------------------
cat > "${APP}/Contents/MacOS/imefuse" <<'LAUNCH'
#!/bin/bash
# imefuse launcher: locate a python3, then start the local GUI.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${HERE}/Resources${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1

PY=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$(command -v python3 || true)"; do
  if [ -n "${cand}" ] && [ -x "${cand}" ]; then PY="${cand}"; break; fi
done

if [ -z "${PY}" ]; then
  osascript -e 'display alert "imefuse" message "没找到 python3。请先安装 Command Line Tools：xcode-select --install" as critical' >/dev/null 2>&1 || true
  exit 1
fi

"${PY}" -m imefuse.cli gui "$@"
status=$?
if [ ${status} -ne 0 ]; then
  osascript -e 'display alert "imefuse" message "启动失败（退出码 '"${status}"'）。\n可在终端运行以下命令看详细报错：\n\npython3 -m imefuse.cli gui" as critical' >/dev/null 2>&1 || true
fi
exit ${status}
LAUNCH
chmod +x "${APP}/Contents/MacOS/imefuse"

# --- ad-hoc sign so Gatekeeper is less grumpy on the local machine ---------
codesign --force --deep --sign - "${APP}" >/dev/null 2>&1 || \
  echo "    (ad-hoc codesign skipped)"

echo "==> done: ${APP}"
echo "    open ${APP}"
