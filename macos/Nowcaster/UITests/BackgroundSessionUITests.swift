import XCTest

/// Explicitly opted-in installed-app acceptance. Real collection is retained;
/// synthetic training lives only in a marked, separate acceptance root.
@MainActor
final class BackgroundSessionUITests: XCTestCase {
    private var ownedApp: XCUIApplication?
    private var lastObservedStatus: String?

    override func setUpWithError() throws {
        try super.setUpWithError()
        // Do not let XCTest's default monitor inspect or answer unrelated OS prompts.
        addUIInterruptionMonitor(withDescription: "User must dismiss system interruption") { _ in
            XCTFail("An external dialog interrupted acceptance; dismiss it manually before retrying.")
            return true
        }
    }

    override func tearDownWithError() throws {
        if let app = ownedApp, app.state != .notRunning {
            app.activate()
            app.typeKey(.escape, modifierFlags: [])
            app.typeKey("q", modifierFlags: .command)
            XCTAssertTrue(app.wait(for: .notRunning, timeout: 35), "Normal Quit must drain owned work.")
        }
        try super.tearDownWithError()
    }

    private func wait(_ description: String, timeout: TimeInterval = 90, _ condition: @escaping () -> Bool) {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: timeout), .completed, description)
    }

    private func capture(_ app: XCUIApplication, _ name: String) {
        app.activate()
        if app.staticTexts["paperSession.status"].exists { _ = sessionStatus(app) }
        let tree = XCTAttachment(string: app.windows.firstMatch.debugDescription)
        tree.name = name + "-accessibility"; tree.lifetime = .keepAlways; add(tree)
        let shot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        shot.name = name; shot.lifetime = .keepAlways; add(shot)
    }

    private func sessionStatus(_ app: XCUIApplication) -> String {
        let element = app.staticTexts["paperSession.status"]
        let value = element.value as? String ?? element.label
        if value != lastObservedStatus {
            print("SESSION_ACCESSIBILITY value=\(String(reflecting: value)) label=\(String(reflecting: element.label))")
            lastObservedStatus = value
        }
        return value
    }

    private func json(_ path: URL) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: path)) as? [String: Any])
    }

    private func processSnapshot() throws -> [String: Any] {
        do {
            let path = try XCTUnwrap(ProcessInfo.processInfo.environment["NOWCASTER_UI_PROCESS_SNAPSHOT"], "External read-only observer is required: XCTest cannot launch ps.")
            let snapshot = try json(URL(fileURLWithPath: path))
            let observed = try XCTUnwrap(snapshot["observed_at"] as? Double)
            XCTAssertLessThan(abs(Date().timeIntervalSince1970 - observed), 3, "Process observation is unavailable/stale, never equivalent to no children.")
            return snapshot
        } catch {
            XCTFail("Process observation unavailable: \(error). This is not evidence of child exit.")
            throw error
        }
    }

    private func children(_ source: URL, registry: URL) throws -> Set<String> {
        Set(try XCTUnwrap(processSnapshot()["processes"] as? [String]).filter {
            ($0.contains(" start --directory " + source.path) || $0.contains(" background-research --registry-directory " + registry.path))
                && $0.contains("/Applications/Nowcaster.app/Contents/Helpers/")
        })
    }

    private func allInstalledHelpers() throws -> [String] {
        try XCTUnwrap(processSnapshot()["processes"] as? [String]).filter {
            $0.contains("/Applications/Nowcaster.app/Contents/Helpers/")
        }
    }

    private func reopen(_ app: XCUIApplication) {
        app.activate()
        app.menuBars.menuBarItems["Paper Session"].click()
        app.menuItems["Open Nowcaster"].click()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 15))
    }

    private func settings(_ app: XCUIApplication, learning: Bool? = nil, resume: Bool? = nil) {
        app.typeKey(",", modifierFlags: .command)
        let learn = app.descendants(matching: .any)["settings.learning"]
        XCTAssertTrue(learn.waitForExistence(timeout: 10))
        for (id, value) in [("settings.learning", learning), ("settings.resume", resume)] {
            if let value {
                let control = app.descendants(matching: .any)[id]
                if String(describing: control.value ?? "") != (value ? "1" : "0") { control.click() }
                XCTAssertEqual(String(describing: control.value ?? ""), value ? "1" : "0")
            }
        }
        for id in ["settings.login", "settings.notifications"] {
            XCTAssertEqual(String(describing: app.descendants(matching: .any)[id].value ?? ""), "0")
        }
        capture(app, "task6-02-settings")
        app.typeKey("w", modifierFlags: .command)
    }

    private func begin(root: URL?, learning: Bool = true) throws -> XCUIApplication {
        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        XCTAssertEqual(app.state, .notRunning, "Do not displace an existing session.")
        if let root {
            XCTAssertTrue(root.path.contains("/UIAcceptanceFixtures/"))
            XCTAssertTrue(FileManager.default.fileExists(atPath: root.appending(path: "UI-TEST-ONLY.md").path))
            app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"] = root.path
        }
        // Normal launch is essential: screenshot destinations suppress restore.
        ownedApp = app; app.launch(); app.activate()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 30))
        wait("Stopped by default") { app.buttons["paperSession.action"].isEnabled }
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        XCTAssertEqual(sessionStatus(app), "Not started", "Validate the actual static-text value before lifecycle predicates.")
        capture(app, "task6-01-installed-stopped")
        settings(app, learning: learning, resume: false)
        app.buttons["paperSession.action"].click()
        capture(app, "task6-03-starting")
        XCTAssertNotEqual(sessionStatus(app), "Needs attention", app.windows.firstMatch.debugDescription)
        return app
    }

    private func action(_ title: String, app: XCUIApplication) {
        wait("Session action becomes " + title, timeout: 120) {
            let button = app.buttons["paperSession.action"]
            return button.exists && button.isEnabled && button.label == title
        }
    }

    private func awaitOwnedWorkers(_ app: XCUIApplication, root: URL, source: URL, registry: URL) throws {
        _ = try processSnapshot()
        wait("Registered campaign and native-owned worker", timeout: 180) {
            if self.sessionStatus(app) == "Needs attention" { return true }
            return (try? self.json(root.appending(path: "paper-session.json"))["campaignHash"] as? String) != nil
                && ((try? self.children(source, registry: registry).count) ?? 0) >= 2
        }
        capture(app, "task6-worker-readiness")
        XCTAssertNotEqual(sessionStatus(app), "Needs attention", app.windows.firstMatch.debugDescription)
        XCTAssertGreaterThanOrEqual(try children(source, registry: registry).count, 2)
    }

    func testOptInInstalledRealTenMinuteBackgroundLifecycle() throws {
        let environment = ProcessInfo.processInfo.environment
        try XCTSkipUnless(environment["NOWCASTER_UI_BACKGROUND_REAL"] == "1")
        continueAfterFailure = false
        let root = URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_REAL_ROOT"]))
        XCTAssertEqual(root.lastPathComponent, "Nowcaster")
        XCTAssertEqual(root.deletingLastPathComponent().lastPathComponent, "Application Support")
        let source = root.appending(path: "PaperResearch/paper-desk-v1")
        let registry = root.appending(path: "BackgroundResearch/registry")
        let protocolURL = source.appending(path: "protocol.json")
        let originalProtocol = try Data(contentsOf: protocolURL)
        let ledgers = try FileManager.default.contentsOfDirectory(at: source, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "jsonl" }
        let prefixes = try Dictionary(uniqueKeysWithValues: ledgers.map { ($0, try Data(contentsOf: $0)) })
        let observations = source.appending(path: "observations.jsonl")
        let app = try begin(root: nil)
        try awaitOwnedWorkers(app, root: root, source: source, registry: registry)
        action("Pause", app: app)
        app.staticTexts["sidebar.research"].click()
        wait("Honest Waiting state") { self.sessionStatus(app) == "Waiting for eligible data" }
        let registered = try XCTUnwrap(try json(root.appending(path: "paper-session.json"))["campaignHash"] as? String)
        let waiting = try XCTUnwrap(try json(registry.appending(path: "status.json"))[registered] as? [String: Any])
        XCTAssertTrue(["idle", "waiting"].contains(waiting["state"] as? String ?? ""))
        XCTAssertTrue((waiting["reason"] as? String ?? "").contains("eligible"))
        XCTAssertEqual(waiting["attempt_count"] as? Int, 0, "Insufficient real history must not invent training.")
        capture(app, "task6-04-real-waiting")
        app.staticTexts["sidebar.tradeDesk"].click()
        let identities = try children(source, registry: registry)
        XCTAssertFalse(identities.isEmpty)
        let preferences = try json(root.appending(path: "paper-session.json"))
        let campaign = try XCTUnwrap(preferences["campaignHash"] as? String)
        let beforeClose = try Data(contentsOf: observations)
        let closed = Date()
        app.typeKey("w", modifierFlags: .command)
        XCTAssertTrue(app.windows.firstMatch.waitForNonExistence(timeout: 10))
        XCTAssertNotEqual(app.state, .notRunning)
        print("BACKGROUND_CLOSE \(closed.ISO8601Format()) campaign=\(campaign) processes=\(identities.sorted())")
        // Timer leaves the run loop and app responsive; no collection is simulated.
        wait("Ten minutes with the window closed", timeout: 620) { Date().timeIntervalSince(closed) >= 600 }
        XCTAssertEqual(try children(source, registry: registry), identities, "Reopening must retain the exact processes and birth times.")
        let retained = try Data(contentsOf: observations)
        XCTAssertTrue(retained.starts(with: beforeClose))
        var bySymbol: [String: [(Date, Date)]] = [:]
        let fractional = ISO8601DateFormatter(); fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let seconds = ISO8601DateFormatter()
        for line in retained.dropFirst(beforeClose.count).split(separator: 10) {
            let row = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(line)) as? [String: Any])
            let symbol = try XCTUnwrap(row["symbol"] as? String)
            let providerText = try XCTUnwrap(row["provider_at"] as? String)
            let receivedText = try XCTUnwrap(row["received_at"] as? String)
            let provider = try XCTUnwrap(fractional.date(from: providerText) ?? seconds.date(from: providerText))
            let received = try XCTUnwrap(fractional.date(from: receivedText) ?? seconds.date(from: receivedText))
            bySymbol[symbol, default: []].append((provider, received))
        }
        for symbol in ["BTCUSDT", "ETHUSDT"] {
            let rows = bySymbol[symbol, default: []]
            XCTAssertTrue(rows.contains { $0.0 > closed && $0.1 > closed }, "Both provider and receipt timestamps must advance for " + symbol)
            print("BACKGROUND_RECEIPTS \(symbol) count=\(rows.count) provider=\(rows.last?.0.ISO8601Format() ?? "missing") receipt=\(rows.last?.1.ISO8601Format() ?? "missing") maxLatency=\(rows.map { $0.1.timeIntervalSince($0.0) }.max() ?? -1)")
        }
        reopen(app)
        XCTAssertEqual(try children(source, registry: registry), identities)
        capture(app, "task6-05-reopened-fresh")
        app.buttons["tradeDesk.asset.ETHUSDT"].click()
        capture(app, "task6-06-selected-ether")
        app.buttons["paperSession.action"].click(); action("Start", app: app)
        wait("Pause exits collection and research") { (try? self.children(source, registry: registry).isEmpty) == true }
        let paused = try Data(contentsOf: observations)
        let pausedAt = Date()
        wait("Pause stays stopped", timeout: 20) { Date().timeIntervalSince(pausedAt) >= 10 }
        XCTAssertEqual(try Data(contentsOf: observations), paused)
        capture(app, "task6-07-paused")
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 35))
        app.launch(); action("Start", app: app)
        XCTAssertTrue(try children(source, registry: registry).isEmpty)
        XCTAssertEqual(try json(root.appending(path: "paper-session.json"))["campaignHash"] as? String, campaign)
        capture(app, "task6-08-relaunch-stopped")
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        for (url, prefix) in prefixes { XCTAssertTrue(try Data(contentsOf: url).starts(with: prefix), url.lastPathComponent) }
        print("BACKGROUND_COMPLETE \(Date().ISO8601Format()) protocolAndLedgerPrefixesPreserved=true")
    }

    func testOptInSyntheticTrainingCheckpointAndAuthenticatedResumeWithClosedWindow() throws {
        let environment = ProcessInfo.processInfo.environment
        try XCTSkipUnless(environment["NOWCASTER_UI_BACKGROUND_SYNTHETIC"] == "1")
        continueAfterFailure = false
        let root = URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_BACKGROUND_STORAGE_ROOT"]))
        let source = root.appending(path: "PaperResearch/paper-desk-v1")
        let registry = root.appending(path: "BackgroundResearch/registry")
        let protocolURL = source.appending(path: "protocol.json")
        let originalProtocol = try Data(contentsOf: protocolURL)
        let originalReceipts = try Data(contentsOf: source.appending(path: "observations.jsonl"))
        let app = try begin(root: root)
        try awaitOwnedWorkers(app, root: root, source: source, registry: registry)
        let beforePreferences = try json(root.appending(path: "paper-session.json"))
        let beforeCampaign = try XCTUnwrap(beforePreferences["campaignHash"] as? String)
        let beforeStatus = try XCTUnwrap(try json(registry.appending(path: "status.json"))[beforeCampaign] as? [String: Any])
        let beforeAttempts = beforeStatus["attempt_count"] as? Int ?? 0
        app.typeKey("w", modifierFlags: .command)
        XCTAssertTrue(app.windows.firstMatch.waitForNonExistence(timeout: 10))
        var retainedStatus: [String: Any] = [:]
        wait("Real synthetic training advances and checkpoints while the window is closed", timeout: 240) {
            guard let prefs = try? self.json(root.appending(path: "paper-session.json")),
                  let campaign = prefs["campaignHash"] as? String,
                  let all = try? self.json(registry.appending(path: "status.json")),
                  let status = all[campaign] as? [String: Any],
                  (status["attempt_count"] as? Int ?? 0) > beforeAttempts,
                  status["last_checkpoint"] as? String != nil else { return false }
            retainedStatus = status
            return true
        }
        let preferences = try json(root.appending(path: "paper-session.json"))
        let campaign = try XCTUnwrap(preferences["campaignHash"] as? String)
        let runtime = try XCTUnwrap(preferences["runtimeCodeIdentity"] as? String)
        let batch = try XCTUnwrap(retainedStatus["batch_id"] as? String)
        let controls = registry.appending(path: "controls")
        let originalOwners = try FileManager.default.contentsOfDirectory(at: controls, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasSuffix(".ownership.json") }
        XCTAssertEqual(originalOwners.count, 1)
        let owner = try json(try XCTUnwrap(originalOwners.first))
        XCTAssertEqual(owner["campaign_hash"] as? String, campaign)
        let identities = try children(source, registry: registry)
        reopen(app)
        XCTAssertEqual(try children(source, registry: registry), identities)
        app.staticTexts["sidebar.research"].click()
        capture(app, "task6-09-synthetic-training-checkpoint")
        settings(app, resume: true)
        let prefix = try Data(contentsOf: registry.appending(path: "events.jsonl"))
        let unrelatedPID = try XCTUnwrap(processSnapshot()["unrelated_pid"] as? Int)
        let quitStarted = Date()
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 35))
        XCTAssertLessThanOrEqual(Date().timeIntervalSince(quitStarted), 30)
        XCTAssertEqual(try processSnapshot()["unrelated_pid"] as? Int, unrelatedPID)
        XCTAssertEqual(try processSnapshot()["unrelated_alive"] as? Bool, true, "Quit must leave an unrelated process alive.")
        print("SYNTHETIC_NATIVE_QUIT seconds=\(Date().timeIntervalSince(quitStarted)) unrelatedPID=\(unrelatedPID) alive=true")
        wait("Quit exits all installed helper descendants, including training children") {
            (try? self.allInstalledHelpers().isEmpty) == true
        }
        app.launch()
        wait("Opted-in resume owns a fresh execution with the same campaign", timeout: 180) {
            let owners = (try? FileManager.default.contentsOfDirectory(at: controls, includingPropertiesForKeys: nil)) ?? []
            return owners.filter { $0.lastPathComponent.hasSuffix(".ownership.json") }.count == 2
        }
        action("Pause", app: app)
        let resumed = try json(root.appending(path: "paper-session.json"))
        XCTAssertEqual(resumed["campaignHash"] as? String, campaign)
        XCTAssertEqual(resumed["runtimeCodeIdentity"] as? String, runtime)
        let status = try XCTUnwrap(try json(registry.appending(path: "status.json"))[campaign] as? [String: Any])
        XCTAssertEqual(status["batch_id"] as? String, batch)
        XCTAssertGreaterThanOrEqual(status["attempt_count"] as? Int ?? 0, retainedStatus["attempt_count"] as? Int ?? 0)
        XCTAssertTrue(try Data(contentsOf: registry.appending(path: "events.jsonl")).starts(with: prefix))
        let owners = try FileManager.default.contentsOfDirectory(at: controls, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasSuffix(".ownership.json") }
        let second = try json(try XCTUnwrap(owners.first { !originalOwners.contains($0) }))
        XCTAssertNotEqual(second["run_id"] as? String, owner["run_id"] as? String)
        XCTAssertNotEqual(second["nonce"] as? String, owner["nonce"] as? String)
        XCTAssertEqual(second["campaign_hash"] as? String, campaign)
        capture(app, "task6-10-synthetic-authenticated-resume")
        settings(app, resume: false)
        app.buttons["paperSession.action"].click(); action("Start", app: app)
        wait("Pause exits all installed helper descendants") { (try? self.allInstalledHelpers().isEmpty) == true }
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        XCTAssertTrue(try Data(contentsOf: source.appending(path: "observations.jsonl")).starts(with: originalReceipts))
        print("SYNTHETIC_NATIVE_CHECKPOINT campaign=\(campaign) batch=\(batch) runtime=\(runtime) retained=\(retainedStatus)")
    }

    func testOptInInstalledNarrowAppearanceAndKeyboardEvidence() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["NOWCASTER_UI_BACKGROUND_VISUAL"] == "1")
        continueAfterFailure = false
        for appearance in ["light", "dark"] {
            let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
            try isolatePaperAcceptance(app)
            ownedApp = app
            app.launchArguments = ["--destination=tradeDesk", "--ui-minimum", "--ui-" + appearance]
            app.launch(); app.activate()
            action("Start", app: app)
            wait("Minimum outer window") {
                abs(app.windows.firstMatch.frame.width - 820) < 1 && abs(app.windows.firstMatch.frame.height - 620) < 1
            }
            XCTAssertTrue(app.buttons["paperSession.action"].isHittable)
            capture(app, "task6-11-installed-minimum-" + appearance)
            app.buttons["tradeDesk.asset.BTCUSDT"].click()
            XCTAssertTrue(app.staticTexts["tradeDesk.detail.title"].waitForExistence(timeout: 10))
            capture(app, "task6-12-installed-selection-" + appearance)
            app.buttons["tradeDesk.closeDetail"].click()
            app.staticTexts["sidebar.tradeDesk"].click()
            app.typeKey(.downArrow, modifierFlags: [])
            XCTAssertTrue(app.staticTexts["sidebar.markets"].exists)
            app.typeKey(.downArrow, modifierFlags: [])
            XCTAssertTrue(app.staticTexts["history.explanation"].waitForExistence(timeout: 10))
            app.typeKey(.downArrow, modifierFlags: [])
            XCTAssertTrue(app.staticTexts["research.resources"].waitForExistence(timeout: 10))
            capture(app, "task6-13-installed-keyboard-research-" + appearance)
            app.typeKey("q", modifierFlags: .command)
            XCTAssertTrue(app.wait(for: .notRunning, timeout: 35))
        }
    }
}
