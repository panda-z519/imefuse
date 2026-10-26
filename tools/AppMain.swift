// imefuse app shell — a real window around the local web UI.
//
// The Python side serves the interface on 127.0.0.1; this shell starts it as a
// child process, waits for it to answer, and shows it in a WKWebView.  That
// keeps the bundle self-contained (no browser needed) and gives proper macOS
// behaviour: a real window, a Dock icon that reopens it, ⌘Q that stops the
// server.
import Cocoa
import WebKit

let PORTS = Array(8770...8789)

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var url: URL?

    // MARK: lifecycle

    func applicationDidFinishLaunching(_ note: Notification) {
        buildWindow()
        guard let python = findPython() else {
            fail("找不到 python3。请先安装 Command Line Tools：\n\nxcode-select --install")
            return
        }
        startServer(python)
        waitForServer(attempts: 80)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window?.makeKeyAndOrderFront(nil) }
        return true
    }

    func applicationWillTerminate(_ note: Notification) {
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

        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: frame, configuration: config)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func showLoading(_ text: String) {
        let html = """
        <html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        background:#14161a;color:#8b93a1;font:14px -apple-system,"PingFang SC",sans-serif}
        </style></head><body><div>\(text)</div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func fail(_ message: String) {
        showLoading(message)
        let alert = NSAlert()
        alert.messageText = "imefuse 启动失败"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.runModal()
    }

    // MARK: python

    func findPython() -> String? {
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return nil
    }

    func resourcesDir() -> String {
        Bundle.main.bundlePath + "/Contents/Resources"
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
        env["PYTHONPATH"] = resourcesDir()
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
        } catch {
            fail("启动 Python 服务失败：\(error.localizedDescription)")
        }
    }

    func waitForServer(attempts: Int) {
        showLoading("正在启动…")
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.poll(remaining: attempts)
        }
    }

    func poll(remaining: Int) {
        guard remaining > 0 else {
            DispatchQueue.main.async { self.fail("服务没有起来。\n\n日志：\(self.dataDir())/app.log") }
            return
        }
        for port in PORTS {
            if let u = URL(string: "http://127.0.0.1:\(port)/api/status"), answers(u) {
                DispatchQueue.main.async { self.load(u) }
                return
            }
        }
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.poll(remaining: remaining - 1)
        }
    }

    func answers(_ url: URL) -> Bool {
        var request = URLRequest(url: url)
        request.timeoutInterval = 0.4
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: request) { _, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200 { ok = true }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 0.8)
        return ok
    }

    func load(_ u: URL) {
        url = u
        window.title = "imefuse — \(u.absoluteString)"
        webView.load(URLRequest(url: u))
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
