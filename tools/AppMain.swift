// imefuse app shell — a real window around the local web UI.
//
// The Python side serves the interface on 127.0.0.1; this shell starts it as a
// child process, waits until the port accepts connections, and shows it in a
// WKWebView.  That keeps the bundle self-contained (no browser needed) and
// gives proper macOS behaviour: a real window, a Dock icon that reopens it,
// ⌘Q that stops the server.
import Cocoa
import Darwin
import WebKit

let PORTS = Array(8770...8789)

func debugLog(_ message: String) {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    let dir = home + "/Library/Application Support/imefuse"
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    let path = dir + "/app.log"
    // O_APPEND keeps each line atomic even though the Python child writes here too
    let fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
    guard fd >= 0 else { return }
    defer { close(fd) }
    let line = "[shell] \(message)\n"
    _ = line.withCString { write(fd, $0, strlen($0)) }
}

/// Plain TCP connect — no URLSession, so ATS and run-loop rules cannot bite.
func portOpen(_ port: Int, timeoutMs: Int32 = 250) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { return false }
    defer { close(fd) }

    var tv = timeval(tv_sec: 0, tv_usec: timeoutMs * 1000)
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(UInt16(port).bigEndian)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")

    let rc = withUnsafePointer(to: &addr) { ptr in
        ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            connect(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return rc == 0
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var currentURL: URL?

    // MARK: lifecycle

    func applicationDidFinishLaunching(_ note: Notification) {
        debugLog("launched, bundle=\(Bundle.main.bundlePath)")
        buildWindow()
        guard let python = findPython() else {
            fail("找不到 python3。请先安装 Command Line Tools：\n\nxcode-select --install")
            return
        }
        debugLog("python=\(python)")
        startServer(python)
        poll(remaining: 100)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window?.makeKeyAndOrderFront(nil) }
        return true
    }

    func applicationWillTerminate(_ note: Notification) {
        debugLog("terminating, stopping server")
        server?.terminate()
    }

    // MARK: ui

    func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1120, height: 820)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "imefuse"
        window.center()
        window.setFrameAutosaveName("imefuse.main")

        webView = WKWebView(frame: frame, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = self
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        showMessage("正在启动…")
    }

    func showMessage(_ text: String) {
        let html = """
        <html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        background:#14161a;color:#8b93a1;font:14px -apple-system,"PingFang SC",sans-serif;
        text-align:center;white-space:pre-wrap;padding:40px}
        </style></head><body><div>\(text)</div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func fail(_ message: String) {
        debugLog("FAIL: \(message)")
        showMessage(message)
        let alert = NSAlert()
        alert.messageText = "imefuse 启动失败"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.runModal()
    }

    // MARK: python

    func findPython() -> String? {
        for path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return nil
    }

    func dataDir() -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let dir = home + "/Library/Application Support/imefuse"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        return dir
    }

    func startServer(_ python: String) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = ["-m", "imefuse.cli", "gui", "--port", String(PORTS[0])]

        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = Bundle.main.bundlePath + "/Contents/Resources"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["IMEFUSE_NO_BROWSER"] = "1"
        proc.environment = env

        let logPath = dataDir() + "/app.log"
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        if let handle = FileHandle(forWritingAtPath: logPath) {
            handle.seekToEndOfFile()
            proc.standardOutput = handle
            proc.standardError = handle
        }
        do {
            try proc.run()
            server = proc
            debugLog("server pid=\(proc.processIdentifier)")
        } catch {
            fail("启动 Python 服务失败：\(error.localizedDescription)")
        }
    }

    func poll(remaining: Int) {
        guard remaining > 0 else {
            let msg = "服务没有起来（试了 \(PORTS.first!)–\(PORTS.last!) 端口）。\n\n日志：\(dataDir())/app.log"
            DispatchQueue.main.async { self.fail(msg) }
            return
        }
        for port in PORTS where portOpen(port) {
            let url = URL(string: "http://127.0.0.1:\(port)/")!
            debugLog("port \(port) is open -> loading \(url.absoluteString)")
            DispatchQueue.main.async { self.load(url) }
            return
        }
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.poll(remaining: remaining - 1)
        }
    }

    func load(_ url: URL) {
        currentURL = url
        window.title = "imefuse — \(url.absoluteString)"
        webView.load(URLRequest(url: url))
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        debugLog("page loaded ok")
    }

    func webView(_ webView: WKWebView,
                 didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        debugLog("page load failed: \(error.localizedDescription)")
        showMessage("页面加载失败：\(error.localizedDescription)\n\n地址：\(currentURL?.absoluteString ?? "?")")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
