import AppKit
import Foundation
import WebKit
import UniformTypeIdentifiers

private struct ControllerEvent: Decodable, Sendable {
    let event: String
    let message: String?
    let url: String?
    let data_root: String?
    let log_path: String?
}

/// Owns exactly one controller. Its Python child owns the local server lifecycle.
private final class ControllerRun: @unchecked Sendable {
    let process = Process()
    let logURL: URL
    private let output = Pipe()
    private let errors = Pipe()
    private let readers = DispatchGroup()
    private let logLock = NSLock()
    private let logFile: FileHandle

    init(resources: URL) throws {
        #if arch(arm64)
        let architecture = "arm64"
        #elseif arch(x86_64)
        let architecture = "x86_64"
        #else
        #error("Only Apple Silicon and Intel Macs are supported.")
        #endif
        let python = resources.appendingPathComponent("python/\(architecture)/bin/python3")
        let controller = resources.appendingPathComponent("app/scripts/macos_launcher.py")
        let files = FileManager.default
        guard files.isExecutableFile(atPath: python.path), files.fileExists(atPath: controller.path) else {
            throw NSError(domain: "CavityLab", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "アプリに必要な実行ファイルが見つかりません。配布ファイルを展開し直してください。"])
        }
        let logs = files.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/CavityLab", isDirectory: true)
        try files.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("launcher-\(UUID().uuidString).log")
        guard files.createFile(atPath: logURL.path, contents: nil) else {
            throw NSError(domain: "CavityLab", code: 2, userInfo: [NSLocalizedDescriptionKey: "起動ログを作成できません。"])
        }
        logFile = try FileHandle(forWritingTo: logURL)
        process.executableURL = python
        process.arguments = ["-B", "-X", "utf8", controller.path]
        process.currentDirectoryURL = resources.appendingPathComponent("app", isDirectory: true)
        var environment = ProcessInfo.processInfo.environment
        environment["CAVITY_MAC_RESOURCE_DIR"] = resources.path
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.removeValue(forKey: "PYTHONHOME")
        environment.removeValue(forKey: "PYTHONPATH")
        process.environment = environment
        process.standardOutput = output
        process.standardError = errors
        process.standardInput = FileHandle.nullDevice
    }

    deinit { try? logFile.close() }

    func start(event: @escaping @Sendable (ControllerEvent) -> Void,
               finished: @escaping @Sendable (Int32) -> Void) throws {
        readers.enter()
        readers.enter()
        process.terminationHandler = { [self] child in
            readers.notify(queue: .main) {
                self.process.terminationHandler = nil
                finished(child.terminationStatus)
            }
        }
        do { try process.run() }
        catch { readers.leave(); readers.leave(); throw error }
        DispatchQueue.global(qos: .utility).async { [self] in
            defer { readers.leave() }
            var pending = Data()
            while true {
                let data = output.fileHandleForReading.availableData
                if data.isEmpty { break }
                appendLog(data)
                pending.append(data)
                while let newline = pending.firstIndex(of: 10) {
                    let line = pending.prefix(upTo: newline)
                    pending.removeSubrange(...newline)
                    if let message = try? JSONDecoder().decode(ControllerEvent.self, from: line) { event(message) }
                }
                // Malformed output must not grow memory indefinitely during setup.
                if pending.count > 1_048_576 { pending.removeAll(keepingCapacity: false) }
            }
            if !pending.isEmpty, let message = try? JSONDecoder().decode(ControllerEvent.self, from: pending) { event(message) }
        }
        DispatchQueue.global(qos: .utility).async { [self] in
            defer { readers.leave() }
            while true {
                let data = errors.fileHandleForReading.availableData
                if data.isEmpty { break }
                appendLog(data)
            }
        }
    }

    func interrupt() {
        if process.isRunning { process.interrupt() }
    }

    private func appendLog(_ data: Data) {
        logLock.lock()
        defer { logLock.unlock() }
        try? logFile.write(contentsOf: data)
    }
}

@MainActor
private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var setupView: NSView!
    private let statusLabel = NSTextField(wrappingLabelWithString: "起動の準備をしています…")
    private let progress = NSProgressIndicator()
    private let footer = NSTextField(labelWithString: "この Mac で計算・保存します。")
    private var retryButton: NSButton!
    private var homeButton: NSButton!
    private var logButton: NSButton!
    private var run: ControllerRun?
    private var runID = UUID()
    private var serviceURL: URL?
    private var dataURL: URL?
    private var logURL: URL?
    private var failed = false
    private var terminating = false
    private var pendingDownloadCancellations = 0
    private var terminationReplySent = false
    private var logWindow: NSWindow?
    private var logText: NSTextView?
    private struct DownloadDestination {
        let download: WKDownload
        let temporary: URL
        let destination: URL
    }
    private var downloads: [ObjectIdentifier: DownloadDestination] = [:]

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        makeWindow()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startController()
    }

    private func makeMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let app = NSMenu(title: "キャビティ FEM")
        app.addItem(withTitle: "キャビティ FEM について", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "隠す", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "キャビティ FEM を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app
        let editItem = NSMenuItem()
        bar.addItem(editItem)
        let edit = NSMenu(title: "編集")
        edit.addItem(withTitle: "取り消す", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "切り取り", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "ペースト", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "すべて選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let viewItem = NSMenuItem()
        bar.addItem(viewItem)
        let view = NSMenu(title: "表示")
        for (title, action, key) in [
            ("解析画面に戻る", #selector(goHome), "1"),
            ("計算データを Finder で表示", #selector(showData), ""),
            ("ログを開く", #selector(showLog), "l"),
            ("起動を再試行", #selector(retry), "")
        ] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.target = self
            view.addItem(item)
        }
        viewItem.submenu = view
        NSApp.mainMenu = bar
    }

    private func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "キャビティ FEM"
        window.minSize = NSSize(width: 620, height: 500)
        window.center()
        window.delegate = self
        window.isReleasedWhenClosed = false
        guard let content = window.contentView else { return }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isHidden = true
        webView.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(webView)
        setupView = NSView()
        setupView.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(setupView)
        let title = NSTextField(labelWithString: "解析環境を準備しています")
        title.font = .systemFont(ofSize: 20, weight: .semibold)
        statusLabel.font = .systemFont(ofSize: 14)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.maximumNumberOfLines = 0
        progress.style = .spinning
        progress.controlSize = .regular
        progress.isIndeterminate = true
        let detail = NSTextField(wrappingLabelWithString: "初回は必要なソフトウェアを準備するため、数分かかることがあります。このウィンドウを開いたままお待ちください。")
        detail.font = .systemFont(ofSize: 12)
        detail.textColor = .secondaryLabelColor
        let setup = NSStackView(views: [title, progress, statusLabel, detail])
        setup.orientation = .vertical
        setup.alignment = .leading
        setup.spacing = 18
        setup.translatesAutoresizingMaskIntoConstraints = false
        setupView.addSubview(setup)
        homeButton = NSButton(title: "解析画面に戻る", target: self, action: #selector(goHome))
        logButton = NSButton(title: "ログを開く", target: self, action: #selector(showLog))
        retryButton = NSButton(title: "再試行", target: self, action: #selector(retry))
        let quit = NSButton(title: "終了", target: NSApp, action: #selector(NSApplication.terminate(_:)))
        let buttons = NSStackView(views: [homeButton, logButton, retryButton, quit])
        buttons.spacing = 8
        let bottom = NSStackView(views: [footer, buttons])
        bottom.distribution = .fill
        bottom.alignment = .centerY
        bottom.spacing = 16
        bottom.translatesAutoresizingMaskIntoConstraints = false
        footer.font = .systemFont(ofSize: 11)
        footer.textColor = .secondaryLabelColor
        footer.lineBreakMode = .byTruncatingMiddle
        footer.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        content.addSubview(bottom)
        NSLayoutConstraint.activate([
            bottom.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 14),
            bottom.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -14),
            bottom.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -9),
            bottom.heightAnchor.constraint(equalToConstant: 30),
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: bottom.topAnchor, constant: -8),
            setupView.topAnchor.constraint(equalTo: webView.topAnchor),
            setupView.leadingAnchor.constraint(equalTo: webView.leadingAnchor),
            setupView.trailingAnchor.constraint(equalTo: webView.trailingAnchor),
            setupView.bottomAnchor.constraint(equalTo: webView.bottomAnchor),
            setup.centerXAnchor.constraint(equalTo: setupView.centerXAnchor),
            setup.centerYAnchor.constraint(equalTo: setupView.centerYAnchor),
            setup.widthAnchor.constraint(equalToConstant: 480)
        ])
        updateButtons()
    }

    private func startController() {
        guard run?.process.isRunning != true, !terminating else { return }
        let identifier = UUID()
        runID = identifier
        failed = false
        serviceURL = nil
        dataURL = nil
        showSetup("起動の準備をしています…", busy: true)
        do {
            guard let resources = Bundle.main.resourceURL else {
                throw NSError(domain: "CavityLab", code: 3, userInfo: [NSLocalizedDescriptionKey: "アプリの構成ファイルが見つかりません。"])
            }
            let controller = try ControllerRun(resources: resources)
            run = controller
            logURL = controller.logURL
            updateButtons()
            try controller.start(event: { [weak self] event in
                DispatchQueue.main.async { self?.receive(event, identifier: identifier) }
            }, finished: { [weak self] code in
                DispatchQueue.main.async { self?.controllerFinished(code, identifier: identifier) }
            })
        } catch {
            run = nil
            failed = true
            showSetup(error.localizedDescription, busy: false)
        }
        updateButtons()
    }

    private func receive(_ event: ControllerEvent, identifier: UUID) {
        guard identifier == runID, !terminating else { return }
        if let path = event.log_path, path.hasPrefix("/") { logURL = URL(fileURLWithPath: path) }
        if let path = event.data_root, path.hasPrefix("/") { dataURL = URL(fileURLWithPath: path, isDirectory: true) }
        switch event.event {
        case "status":
            if serviceURL == nil { showSetup(event.message ?? "準備しています…", busy: true) }
        case "ready":
            guard let address = event.url, let url = URL(string: address),
                  url.scheme == "http", url.host == "127.0.0.1", url.port != nil,
                  url.user == nil, url.password == nil else {
                failed = true
                showSetup("ローカル解析画面の接続先を確認できません。ログを確認してください。", busy: false)
                run?.interrupt()
                return
            }
            serviceURL = url
            progress.stopAnimation(nil)
            setupView.isHidden = true
            webView.isHidden = false
            footer.stringValue = "この Mac で計算・保存しています。終了すると計算も停止します。"
            webView.load(URLRequest(url: url))
        case "error":
            failed = true
            serviceURL = nil
            showSetup(event.message ?? "起動できませんでした。ログを確認してください。", busy: false)
        default: break
        }
        updateButtons()
    }

    private func controllerFinished(_ code: Int32, identifier: UUID) {
        guard identifier == runID else { return }
        run = nil
        if terminating { finishTerminationIfReady(); return }
        if !failed {
            failed = true
            showSetup("解析用の処理が終了しました（終了コード \(code)）。ログを確認して再試行してください。", busy: false)
        }
        serviceURL = nil
        updateButtons()
    }

    private func showSetup(_ text: String, busy: Bool) {
        webView?.isHidden = true
        setupView?.isHidden = false
        statusLabel.stringValue = text
        if busy { progress.startAnimation(nil) } else { progress.stopAnimation(nil) }
        progress.isHidden = !busy
        footer.stringValue = terminating ? "このアプリが開始した処理の終了を待っています。" : "この Mac で計算・保存します。"
    }

    private func updateButtons() {
        homeButton?.isEnabled = serviceURL != nil && !terminating
        logButton?.isEnabled = logURL != nil
        retryButton?.isHidden = !failed
        retryButton?.isEnabled = failed && run?.process.isRunning != true && !terminating
    }

    @objc private func retry() { startController() }

    @objc private func goHome() {
        guard let url = serviceURL, !terminating else { return }
        setupView.isHidden = true
        webView.isHidden = false
        webView.load(URLRequest(url: url))
    }

    @objc private func showData() {
        guard let url = dataURL else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func showLog() {
        guard let url = logURL else { return }
        if logWindow == nil {
            let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 500),
                                 styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            panel.title = "起動・計算ログ"
            panel.isReleasedWhenClosed = false
            let scroll = NSScrollView(frame: panel.contentView!.bounds)
            scroll.autoresizingMask = [.width, .height]
            scroll.hasVerticalScroller = true
            let text = NSTextView(frame: scroll.bounds)
            text.isEditable = false
            text.isSelectable = true
            text.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
            text.autoresizingMask = [.width]
            text.textContainer?.widthTracksTextView = true
            text.isVerticallyResizable = true
            scroll.documentView = text
            panel.contentView?.addSubview(scroll)
            logWindow = panel
            logText = text
            panel.center()
        }
        do {
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            let size = try file.seekToEnd()
            try file.seek(toOffset: size > 2_000_000 ? size - 2_000_000 : 0)
            let data = try file.readToEnd() ?? Data()
            logText?.string = url.path + "\n\n" + (size > 2_000_000 ? "（末尾の約 2 MB を表示）\n" : "") + String(decoding: data, as: UTF8.self)
            logText?.scrollToEndOfDocument(nil)
        } catch { logText?.string = "ログを読み込めませんでした。\n" + error.localizedDescription }
        logWindow?.makeKeyAndOrderFront(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === window { NSApp.terminate(nil); return false }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating { return .terminateLater }
        let controllerIsRunning = run?.process.isRunning == true
        guard controllerIsRunning || !downloads.isEmpty else { return .terminateNow }
        terminating = true
        webView.stopLoading()
        // Explicit cancellation suppresses WKDownload's failure delegate. Its
        // completion must clean our temporary files before the app terminates.
        let activeDownloads = Array(downloads.values)
        downloads.removeAll()
        pendingDownloadCancellations = activeDownloads.count
        for destination in activeDownloads {
            destination.download.cancel { [weak self] _ in
                try? FileManager.default.removeItem(at: destination.temporary)
                guard let self = self else { return }
                self.pendingDownloadCancellations -= 1
                self.finishTerminationIfReady()
            }
        }
        showSetup("計算用の処理を停止しています…", busy: true)
        updateButtons()
        if controllerIsRunning { run?.interrupt() } else { run = nil }
        finishTerminationIfReady()
        return .terminateLater
    }

    private func finishTerminationIfReady() {
        // A cancellation completion may run synchronously. Reply on the next
        // main-queue turn, after applicationShouldTerminate returned .later.
        DispatchQueue.main.async { [weak self] in
            guard let self = self, self.terminating, !self.terminationReplySent,
                  self.run == nil, self.pendingDownloadCancellations == 0 else { return }
            self.terminationReplySent = true
            NSApp.reply(toApplicationShouldTerminate: true)
        }
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url = url, let origin = serviceURL else { return false }
        return url.scheme == "http" && url.host == origin.host && url.port == origin.port && url.user == nil && url.password == nil
    }

    private func showMessage(_ message: String) {
        guard !terminating else { return }
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "閉じる")
        alert.beginSheetModal(for: window)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard isLocal(navigationAction.request.url) else {
            decisionHandler(.cancel)
            if navigationAction.navigationType == .linkActivated { showMessage("このアプリではローカルの解析画面だけを開けます。") }
            return
        }
        if navigationAction.shouldPerformDownload { decisionHandler(.download); return }
        if navigationAction.targetFrame == nil {
            decisionHandler(.cancel)
            webView.load(navigationAction.request)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void) {
        guard isLocal(navigationResponse.response.url) else { decisionHandler(.cancel); return }
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(!navigationResponse.canShowMIMEType || disposition.lowercased().contains("attachment") ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable ([URL]?) -> Void) {
        guard isLocal(frame.request.url) else { completionHandler(nil); return }
        let panel = NSOpenPanel()
        panel.title = "STEP 形状を選択"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.allowedContentTypes = ["step", "stp", "txt"].compactMap { UTType(filenameExtension: $0, conformingTo: .data) }
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(error)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    private func navigationFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled, !terminating else { return }
        showSetup("解析画面を表示できません。下の「解析画面に戻る」で再読み込みできます。\n" + error.localizedDescription, busy: false)
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping @MainActor @Sendable (URL?) -> Void) {
        guard isLocal(response.url), !terminating else { completionHandler(nil); return }
        let panel = NSSavePanel()
        panel.title = "解析結果を保存"
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self = self, result == .OK, let destination = panel.url, !self.terminating else { completionHandler(nil); return }
            // WKDownload requires a nonexistent target; overwrite only after the user's save-panel confirmation.
            let temporary = destination.deletingLastPathComponent().appendingPathComponent(".cavity-download-\(UUID().uuidString)")
            self.downloads[ObjectIdentifier(download)] = DownloadDestination(download: download, temporary: temporary, destination: destination)
            self.footer.stringValue = destination.lastPathComponent + " を保存しています…"
            completionHandler(temporary)
        }
    }

    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse,
                  newRequest request: URLRequest, decisionHandler: @escaping @MainActor @Sendable (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(isLocal(request.url) ? .allow : .cancel)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let file = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        do {
            let files = FileManager.default
            if files.fileExists(atPath: file.destination.path) {
                _ = try files.replaceItemAt(file.destination, withItemAt: file.temporary)
            } else { try files.moveItem(at: file.temporary, to: file.destination) }
            footer.stringValue = file.destination.lastPathComponent + " を保存しました。"
        } catch {
            try? FileManager.default.removeItem(at: file.temporary)
            showMessage("結果を保存できませんでした。\n" + error.localizedDescription)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let file = downloads.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: file.temporary) }
        if (error as NSError).code != NSURLErrorCancelled { showMessage("ダウンロードできませんでした。\n" + error.localizedDescription) }
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = AppDelegate()
    application.setActivationPolicy(.regular)
    application.delegate = delegate
    withExtendedLifetime(delegate) { application.run() }
}
