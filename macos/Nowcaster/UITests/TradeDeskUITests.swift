import XCTest

/// Real-window tests. No injected market results, credentials or notification opt-in.
@MainActor
final class TradeDeskUITests: XCTestCase {
    override func tearDownWithError() throws {
        continueAfterFailure = true
        let app = XCUIApplication()
        if app.state != .notRunning {
            // XCTest failures do not always unwind Swift defers. Stop only the
            // app-owned collector and use normal Quit before runner cleanup.
            let stop = app.buttons["paperSignals.stop"]
            if stop.exists, stop.isEnabled {
                stop.click()
                let stopped = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND enabled == true"),
                                                        object: app.buttons["paperSignals.start"])
                _ = XCTWaiter.wait(for: [stopped], timeout: 30)
            }
            app.typeKey(.escape, modifierFlags: [])
            app.typeKey("q", modifierFlags: .command)
            if !app.wait(for: .notRunning, timeout: 30) { app.terminate() }
        }
        try super.tearDownWithError()
    }

    private func capture(_ app: XCUIApplication, _ name: String) {
        // Window screenshots crop the display and can include an overlapping app.
        app.activate()
        XCTAssertEqual(app.state, .runningForeground)
        let tree = XCTAttachment(string: app.windows.firstMatch.debugDescription)
        tree.name = name + "-accessibility"
        tree.lifetime = .keepAlways
        add(tree)
        let attachment = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func waitEnabled(_ element: XCUIElement, timeout: TimeInterval = 60) {
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND enabled == true"), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: timeout), .completed)
    }

    nonisolated private static func freshSymbols(at url: URL, after prefix: Data, since start: Date) -> Set<String> {
        guard let data = try? Data(contentsOf: url), data.starts(with: prefix) else { return [] }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let seconds = ISO8601DateFormatter()
        return Set(data.dropFirst(prefix.count).split(separator: 10).compactMap { line -> String? in
            guard let row = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any],
                  let symbol = row["symbol"] as? String,
                  let receivedText = row["received_at"] as? String,
                  let providerText = row["provider_at"] as? String,
                  let received = formatter.date(from: receivedText) ?? seconds.date(from: receivedText),
                  let provider = formatter.date(from: providerText) ?? seconds.date(from: providerText),
                  received >= start, received <= Date(), provider <= received,
                  received.timeIntervalSince(provider) <= 15 else { return nil }
            return symbol
        })
    }

    private func choose(_ path: String, button: String, app: XCUIApplication) {
        waitEnabled(app.buttons[button])
        app.buttons[button].click()
        capture(app, "picker-" + button)
        XCTAssertTrue(app.sheets.firstMatch.waitForExistence(timeout: 10), app.debugDescription)
        app.typeKey("g", modifierFlags: [.command, .shift])
        let field = app.textFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10), app.debugDescription)
        field.typeText(path)
        app.typeKey(.return, modifierFlags: [])
        // macOS also exposes a Touch Bar "Open" button; use the actual sheet.
        let open = app.sheets.buttons["Open"].firstMatch
        waitEnabled(open, timeout: 10)
        open.click()
        XCTAssertTrue(app.sheets.firstMatch.waitForNonExistence(timeout: 10))
    }

    func testTradeDeskOpensWithPaperControlsAndNoAutomaticCollection() throws {
        // Catches startup returning to the demo, a missing native window, or
        // collection starting without a user's explicit action.
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 30))
        XCTAssertTrue(app.buttons["paperSignals.start"].waitForExistence(timeout: 30), app.debugDescription)
        XCTAssertFalse(app.buttons["paperSignals.stop"].exists)
        XCTAssertTrue(app.staticTexts["Stand aside"].firstMatch.exists)
        let capture = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        capture.name = "01-native-trade-desk"
        capture.lifetime = .keepAlways
        add(capture)
    }

    func testOptInInstalledAppNormalQuitForUpgrade() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["NOWCASTER_UI_INSTALLED_APP"] == "quit")
        continueAfterFailure = false
        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        guard app.state != .notRunning else { return }
        app.activate()
        XCTAssertFalse(app.buttons["paperSignals.stop"].exists, "Do not replace an app collecting evidence.")
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
    }

    func testOptInInstalledBundleOpensTradeDesk() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["NOWCASTER_UI_INSTALLED_APP"] == "verify")
        continueAfterFailure = false
        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        XCTAssertEqual(app.state, .notRunning, "Quit the installed app normally before verifying its replacement.")
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertFalse(app.buttons["paperSignals.stop"].exists)
        XCTAssertTrue(app.staticTexts["Stand aside"].firstMatch.exists)
        capture(app, "11-installed-application")
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
    }

    /// Explicit opt-in: creates/resumes the real default desk and retains every
    /// observation. Never resets evidence or silently performs network work in CI.
    func testExplicitPaperDeskSetupImportsLiveCollectionAndRelaunch() throws {
        let environment = ProcessInfo.processInfo.environment
        try XCTSkipUnless(environment["NOWCASTER_UI_LIVE_ACCEPTANCE"] == "1",
                          "Opt in to retained public-data collection; see README.")
        let calendar = try XCTUnwrap(environment["NOWCASTER_UI_CALENDAR"])
        let desk = URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_DESK_DIRECTORY"]))
        XCTAssertEqual(desk.lastPathComponent, "paper-desk-v1")
        XCTAssertTrue(FileManager.default.fileExists(atPath: calendar))
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["paperSignals.start"].waitForExistence(timeout: 30))
        if app.buttons["paperSignals.setup"].exists { app.buttons["paperSignals.setup"].click() }
        waitEnabled(app.buttons["paperSignals.start"])
        // The UI runner has its own container; inspect the explicitly selected
        // app directory, not the runner's Application Support directory.
        let protocolURL = desk.appending(path: "protocol.json")
        let originalProtocol = try Data(contentsOf: protocolURL)
        let calendarLedger = desk.appending(path: "day-trader-calendar.jsonl")
        let originalCalendar = try? Data(contentsOf: calendarLedger)
        capture(app, "02-desk-setup")
        XCTAssertEqual(String(describing: try XCTUnwrap(app.switches["paperSignals.notifications"].value)), "0")

        let invalid = FileManager.default.temporaryDirectory.appending(path: "nowcaster-invalid-calendar-\(UUID().uuidString).json")
        try Data("{}".utf8).write(to: invalid)
        defer { try? FileManager.default.removeItem(at: invalid) }
        choose(invalid.path, button: "Import Calendar…", app: app)
        XCTAssertTrue(app.staticTexts["Calendar not imported. Use a current, covered calendar JSON in the documented format; old or conflicting evidence is rejected."].waitForExistence(timeout: 30))
        capture(app, "03-invalid-calendar-rejected")
        choose(calendar, button: "Import Calendar…", app: app)
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertTrue(app.staticTexts["Calendar not imported. Use a current, covered calendar JSON in the documented format; old or conflicting evidence is rejected."].exists)
        XCTAssertEqual(try? Data(contentsOf: calendarLedger), originalCalendar)
        capture(app, "04-uncovered-calendar-rejected")

        // Opening an unregistered directory must fail, not initialize or erase it.
        choose(invalid.deletingLastPathComponent().path, button: "Choose Research Folder…", app: app)
        XCTAssertTrue(app.staticTexts["Choose a registered research directory and an available paper research engine."].waitForExistence(timeout: 30))
        XCTAssertFalse(app.buttons["paperSignals.start"].isEnabled)
        choose(desk.path, button: "Choose Research Folder…", app: app)
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)

        let observationURL = desk.appending(path: "observations.jsonl")
        let previousObservations = (try? Data(contentsOf: observationURL)) ?? Data()
        let startedAt = Date()
        app.buttons["paperSignals.start"].click()
        waitEnabled(app.buttons["paperSignals.stop"])
        let fresh = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            Self.freshSymbols(at: observationURL, after: previousObservations, since: startedAt)
                .isSuperset(of: ["BTCUSDT", "ETHUSDT"])
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [fresh], timeout: 120), .completed, "Both assets need new, timely receipts after Start.")
        XCTAssertTrue(app.staticTexts["Last source observation"].waitForExistence(timeout: 90), app.debugDescription)
        XCTAssertTrue(app.staticTexts["Stand aside"].firstMatch.exists)
        capture(app, "05-live-public-data")
        app.buttons["paperSignals.stop"].click()
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertTrue(app.staticTexts["Not collecting"].exists)
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        let observations = try String(contentsOf: desk.appending(path: "observations.jsonl"), encoding: .utf8)
        XCTAssertTrue(observations.contains("BTCUSDT"))
        XCTAssertTrue(observations.contains("ETHUSDT"))
        capture(app, "06-stopped-retained")

        // Normal Quit, rather than a crash/force-quit, then reopen retained state.
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
        app.launch()
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertFalse(app.buttons["paperSignals.stop"].exists)
        XCTAssertEqual(String(describing: try XCTUnwrap(app.switches["paperSignals.notifications"].value)), "0")
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        XCTAssertEqual(try String(contentsOf: desk.appending(path: "observations.jsonl"), encoding: .utf8), observations)
        capture(app, "07-reopened-with-evidence")
    }

    func testSyntheticCompletedLifecycleIsDisplayedAsHistoryNotALiveTrade() throws {
        let path = try XCTUnwrap(ProcessInfo.processInfo.environment["NOWCASTER_UI_REPLAY_DIRECTORY"] ?? "")
        try XCTSkipIf(path.isEmpty, "Opt in with a retained, synthetic lifecycle fixture.")
        let directory = URL(fileURLWithPath: path)
        try XCTSkipUnless(FileManager.default.fileExists(atPath: directory.appending(path: "UI-TEST-ONLY.md").path))
        let ledger = directory.appending(path: "paper-lifecycles.jsonl")
        let original = try Data(contentsOf: ledger)
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["paperSignals.start"].waitForExistence(timeout: 30))
        choose(path, button: "Choose Research Folder…", app: app)
        capture(app, "synthetic-folder-open-result")
        waitEnabled(app.buttons["paperSignals.start"])
        // Only this marked synthetic fixture receives a synthetic calendar.
        // The live/default directory must never receive fabricated coverage.
        let now = Date(), formatter = ISO8601DateFormatter()
        // The XCTest runner cannot write into another app's Application Support.
        // Keep the source in its writable container and retain it as an attachment.
        let attempt = UUID().uuidString
        let calendar = FileManager.default.temporaryDirectory.appending(path: "ui-test-calendar-\(attempt).json")
        let published = formatter.string(from: now.addingTimeInterval(-60))
        let payload: [String: Any] = ["source": "retained-test-calendar", "revision": "UI-TEST-ONLY-" + UUID().uuidString,
            "published_at": published, "available_at": published,
            "valid_until": formatter.string(from: now.addingTimeInterval(3600)),
            "coverage_starts_at": formatter.string(from: now.addingTimeInterval(-3600)),
            "coverage_ends_at": formatter.string(from: now.addingTimeInterval(3600)),
            "events": [["event_id": "UI-TEST-ONLY", "scheduled_at": formatter.string(from: now.addingTimeInterval(1800)),
                        "available_at": published, "symbols": ["BTCUSDT", "ETHUSDT"], "impact": "high"]]]
        let calendarData = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        try calendarData.write(to: calendar)
        let sourceAttachment = XCTAttachment(data: calendarData, uniformTypeIdentifier: "public.json")
        sourceAttachment.name = "synthetic-calendar-source-" + attempt
        sourceAttachment.lifetime = .keepAlways
        add(sourceAttachment)
        choose(calendar.path, button: "Import Calendar…", app: app)
        XCTAssertTrue(app.staticTexts["Calendar evidence retained. Coverage, age and blackout checks still apply."].waitForExistence(timeout: 30))
        capture(app, "synthetic-calendar-import-NOT-market-evidence")
        let disclosure = app.disclosureTriangles["dayTrader.historicalOutcomes"]
        XCTAssertTrue(disclosure.waitForExistence(timeout: 10), app.debugDescription)
        // On macOS the selectable label only takes focus; use its leading arrow.
        disclosure.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.5)).click()
        let expanded = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == 1 OR value == '1'"), object: disclosure)
        XCTAssertEqual(XCTWaiter.wait(for: [expanded], timeout: 10), .completed)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@ OR value CONTAINS %@", "BTCUSDT · Target", "BTCUSDT · Target")).firstMatch.waitForExistence(timeout: 10), app.windows.firstMatch.debugDescription)
        XCTAssertTrue(app.staticTexts["Current context unavailable"].exists)
        XCTAssertFalse(app.staticTexts["Research entry zone"].exists)
        XCTAssertFalse(app.buttons["paperSignals.stop"].exists)
        XCTAssertEqual(try Data(contentsOf: ledger), original)
        capture(app, "08-synthetic-completed-history-NOT-live-evidence")
    }

    func testOptInSustainedPublicCollectionPreservesEarlierRecords() throws {
        let environment = ProcessInfo.processInfo.environment
        let minutes = Int(environment["NOWCASTER_UI_SOAK_MINUTES"] ?? "0") ?? 0
        try XCTSkipUnless((1...30).contains(minutes), "Set a bounded 1–30 minute public-feed soak explicitly.")
        let desk = URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_DESK_DIRECTORY"]))
        XCTAssertEqual(desk.lastPathComponent, "paper-desk-v1")
        let observations = desk.appending(path: "observations.jsonl")
        let before = (try? Data(contentsOf: observations)) ?? Data()
        let baseline = before.filter { $0 == 10 }.count
        let protocolURL = desk.appending(path: "protocol.json")
        let originalProtocol = try Data(contentsOf: protocolURL)
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        waitEnabled(app.buttons["paperSignals.start"])
        let startedAt = Date()
        let startedUptime = ProcessInfo.processInfo.systemUptime
        app.buttons["paperSignals.start"].click()
        waitEnabled(app.buttons["paperSignals.stop"])
        let collected = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            let data = (try? Data(contentsOf: observations)) ?? Data()
            return ProcessInfo.processInfo.systemUptime - startedUptime >= Double(minutes) * 60
                && data.filter { $0 == 10 }.count >= baseline + minutes * 2
                && Self.freshSymbols(at: observations, after: before, since: max(startedAt, Date().addingTimeInterval(-90)))
                    .isSuperset(of: ["BTCUSDT", "ETHUSDT"])
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [collected], timeout: Double(minutes + 2) * 60), .completed)
        capture(app, "09-sustained-public-collection")
        app.buttons["paperSignals.stop"].click()
        waitEnabled(app.buttons["paperSignals.start"])
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        XCTAssertTrue(try Data(contentsOf: observations).starts(with: before))
        XCTAssertTrue(app.staticTexts["Not collecting"].exists)
        capture(app, "10-soak-stopped-retained")
    }
}
