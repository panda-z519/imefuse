#!/bin/bash
# Build a double-clickable macOS app bundle.
#
#   tools/build_app.sh            -> build/imefuse.app
#
# Preferred: compile tools/AppMain.swift into a real Cocoa app that starts the
# Python server as a child process and shows the UI in a WKWebView window.
# Fallback (no swiftc): ship a shell launcher that starts the server and opens
# the system browser.
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
if [ ! -f "${ICNS}" ] && command -v swiftc >/dev/null 2>&1; then
  echo "==> generating icon (one-off)"
  TMPI="$(mktemp -d)"
  if swiftc -O "${ROOT}/tools/make_icon.swift" -o "${TMPI}/make_icon" 2>/dev/null \
     && "${TMPI}/make_icon" "${TMPI}/AppIcon.iconset" >/dev/null \
     && iconutil -c icns "${TMPI}/AppIcon.iconset" -o "${TMPI}/AppIcon.icns"; then
    cp "${TMPI}/AppIcon.icns" "${ICNS}"
  fi
  rm -rf "${TMPI}"
fi
[ -f "${ICNS}" ] && cp "${ICNS}" "${APP}/Contents/Resources/AppIcon.icns"

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
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict>
</plist>
PLIST

# --- executable: native shell if we can, bash launcher otherwise -----------
MODE="shell"
if command -v swiftc >/dev/null 2>&1; then
  echo "==> compiling native app shell"
  # the toolchain defaults minos to the host OS, which makes the bundle
  # unlaunchable on anything older -- pin it explicitly
  ARCH="$(uname -m)"
  if swiftc -O -target "${ARCH}-apple-macosx11.0" \
       -o "${APP}/Contents/MacOS/imefuse" "${ROOT}/tools/AppMain.swift" 2>/tmp/imefuse-swift.log; then
    MODE="native"
  else
    echo "    swiftc failed, falling back to the browser launcher:"
    sed -n '1,5p' /tmp/imefuse-swift.log | sed 's/^/      /'
  fi
fi

if [ "${MODE}" = "shell" ]; then
cat > "${APP}/Contents/MacOS/imefuse" <<'LAUNCH'
#!/bin/bash
# Fallback launcher: start the server, wait for it, open the system browser.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${HERE}/Resources${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1
export IMEFUSE_NO_BROWSER=1
DATA="${HOME}/Library/Application Support/imefuse"
mkdir -p "${DATA}"
LOG="${DATA}/app.log"

PY=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
  if [ -n "${cand}" ] && [ -x "${cand}" ]; then PY="${cand}"; break; fi
done
if [ -z "${PY}" ]; then
  osascript -e 'display alert "imefuse" message "没找到 python3。请先安装 Command Line Tools：\n\nxcode-select --install" as critical' >/dev/null 2>&1 || true
  exit 1
fi

echo "=== $(date) starting imefuse with ${PY}" >>"${LOG}"
"${PY}" -m imefuse.cli gui "$@" >>"${LOG}" 2>&1 &
SERVER_PID=$!

URL=""
for _ in $(seq 1 60); do
  for p in $(seq 8770 8789); do
    if /usr/bin/curl -s -m 0.4 -o /dev/null "http://127.0.0.1:${p}/api/status" 2>/dev/null; then
      URL="http://127.0.0.1:${p}/"
      break 2
    fi
  done
  sleep 0.25
done

if [ -z "${URL}" ]; then
  osascript -e 'display alert "imefuse 启动失败" message "服务没有起来，日志在 ~/Library/Application Support/imefuse/app.log" as critical' >/dev/null 2>&1 || true
  kill "${SERVER_PID}" 2>/dev/null
  exit 1
fi

echo "serving ${URL}" >>"${LOG}"
/usr/bin/open "${URL}" 2>>"${LOG}" || true
wait "${SERVER_PID}"
LAUNCH
chmod +x "${APP}/Contents/MacOS/imefuse"
fi

# --- sign + verify ---------------------------------------------------------
codesign --force --deep --sign - "${APP}" >/dev/null 2>&1 || echo "!! ad-hoc codesign failed" >&2
test -x "${APP}/Contents/MacOS/imefuse" || { echo "!! launcher is not executable" >&2; exit 1; }
codesign -v "${APP}" >/dev/null 2>&1 || { echo "!! bundle is not signed" >&2; exit 1; }

echo "==> done: ${APP}  (${MODE})"
echo "    open ${APP}"
