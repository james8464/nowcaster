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
            let stop = app.buttons["paperSession.action"]
            if stop.exists, stop.isEnabled, stop.label == "Pause" {
                stop.click()
                let stopped = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND enabled == true AND label == 'Start'"),
                                                        object: app.buttons["paperSession.action"])
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

    private func waitSession(_ title: String, app: XCUIApplication) {
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND enabled == true AND label == %@", title),
                                              object: app.buttons["paperSession.action"])
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 60), .completed)
    }

    private func assertNotificationsOff(_ app: XCUIApplication) {
        app.typeKey(",", modifierFlags: .command)
        let toggle = app.descendants(matching: .any)["settings.notifications"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 10))
        XCTAssertEqual(String(describing: toggle.value ?? "missing"), "0")
        app.typeKey("w", modifierFlags: .command)
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
        let dataMenu = app.popUpButtons["tradeDesk.data"]
        waitEnabled(dataMenu)
        dataMenu.click()
        app.menuItems[button].click()
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
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        app.activate()
        defer { app.terminate() }
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 30))
        XCTAssertTrue(app.buttons["paperSession.action"].waitForExistence(timeout: 30), app.debugDescription)
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].exists)
        let capture = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        capture.name = "01-native-trade-desk"
        capture.lifetime = .keepAlways
        add(capture)
        assertNotificationsOff(app)
    }

    func testOptInInstalledAppNormalQuitForUpgrade() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["NOWCASTER_UI_INSTALLED_APP"] == "quit")
        continueAfterFailure = false
        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        guard app.state != .notRunning else { return }
        app.activate()
        XCTAssertNotEqual(app.buttons["paperSession.action"].label, "Pause", "Do not replace an app collecting evidence.")
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
    }

    func testOptInInstalledBundleOpensTradeDesk() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["NOWCASTER_UI_INSTALLED_APP"] == "verify")
        continueAfterFailure = false
        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        XCTAssertEqual(app.state, .notRunning, "Quit the installed app normally before verifying its replacement.")
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        waitEnabled(app.buttons["paperSession.action"])
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].label.contains("Stand aside"))
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
        try isolatePaperAcceptance(app)
        XCTAssertTrue(desk.path.hasPrefix(try XCTUnwrap(app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"]) + "/"))
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["paperSession.action"].waitForExistence(timeout: 30))
        if !FileManager.default.fileExists(atPath: desk.appending(path: "protocol.json").path) {
            app.buttons["tradeDesk.setup"].click()
            app.buttons["paperSignals.setup"].click()
            waitEnabled(app.buttons["paperSignals.setup"])
            app.buttons["Done"].click()
        }
        waitEnabled(app.buttons["paperSession.action"])
        // The UI runner has its own container; inspect the explicitly selected
        // app directory, not the runner's Application Support directory.
        let protocolURL = desk.appending(path: "protocol.json")
        let originalProtocol = try Data(contentsOf: protocolURL)
        let calendarLedger = desk.appending(path: "day-trader-calendar.jsonl")
        let originalCalendar = try? Data(contentsOf: calendarLedger)
        capture(app, "02-desk-setup")
        assertNotificationsOff(app)

        let invalid = FileManager.default.temporaryDirectory.appending(path: "nowcaster-invalid-calendar-\(UUID().uuidString).json")
        try Data("{}".utf8).write(to: invalid)
        defer { try? FileManager.default.removeItem(at: invalid) }
        choose(invalid.path, button: "Import Calendar…", app: app)
        XCTAssertTrue(app.staticTexts["Calendar not imported. Use a current, covered calendar JSON in the documented format; old or conflicting evidence is rejected."].waitForExistence(timeout: 30))
        capture(app, "03-invalid-calendar-rejected")
        choose(calendar, button: "Import Calendar…", app: app)
        waitEnabled(app.buttons["paperSession.action"])
        XCTAssertTrue(app.staticTexts["Calendar not imported. Use a current, covered calendar JSON in the documented format; old or conflicting evidence is rejected."].exists)
        XCTAssertEqual(try? Data(contentsOf: calendarLedger), originalCalendar)
        capture(app, "04-uncovered-calendar-rejected")

        // Opening an unregistered directory must fail, not initialize or erase it.
        choose(invalid.deletingLastPathComponent().path, button: "Choose Research Folder…", app: app)
        XCTAssertTrue(app.staticTexts["Choose a registered research directory and an available paper research engine."].waitForExistence(timeout: 30))
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        choose(desk.path, button: "Choose Research Folder…", app: app)
        waitEnabled(app.buttons["paperSession.action"])
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)

        let observationURL = desk.appending(path: "observations.jsonl")
        let previousObservations = (try? Data(contentsOf: observationURL)) ?? Data()
        let startedAt = Date()
        app.buttons["paperSession.action"].click()
        waitSession("Pause", app: app)
        let fresh = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            Self.freshSymbols(at: observationURL, after: previousObservations, since: startedAt)
                .isSuperset(of: ["BTCUSDT", "ETHUSDT"])
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [fresh], timeout: 120), .completed, "Both assets need new, timely receipts after Start.")
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].label.contains("Stand aside"))
        capture(app, "05-live-public-data")
        app.buttons["paperSession.action"].click()
        waitSession("Start", app: app)
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        let observations = try String(contentsOf: desk.appending(path: "observations.jsonl"), encoding: .utf8)
        XCTAssertTrue(observations.contains("BTCUSDT"))
        XCTAssertTrue(observations.contains("ETHUSDT"))
        capture(app, "06-stopped-retained")

        // Normal Quit, rather than a crash/force-quit, then reopen retained state.
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
        app.launch()
        waitEnabled(app.buttons["paperSession.action"])
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        assertNotificationsOff(app)
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
        try isolatePaperAcceptance(app)
        XCTAssertTrue(directory.path.hasPrefix(try XCTUnwrap(app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"]) + "/"))
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["paperSession.action"].waitForExistence(timeout: 30))
        choose(path, button: "Choose Research Folder…", app: app)
        capture(app, "synthetic-folder-open-result")
        waitEnabled(app.buttons["paperSession.action"])
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
        app.staticTexts["sidebar.history"].click()
        XCTAssertTrue(app.staticTexts["history.explanation"].waitForExistence(timeout: 10))
        capture(app, "synthetic-expanded-history-NOT-market-evidence")
        // AX values are heterogeneous: applying CONTAINS to a numeric value
        // throws inside XCTest's query evaluator. Read the snapshot, then
        // type-check values instead of evaluating that predicate on every node.
        let completedOutcome = app.windows.firstMatch.descendants(matching: .any)
            .allElementsBoundByIndex.contains { element in
                element.label.contains("BTCUSDT · Target")
                    || (element.value as? String)?.contains("BTCUSDT · Target") == true
            }
        XCTAssertTrue(completedOutcome, "Expanded history must expose the retained BTC target outcome.")
        XCTAssertTrue(app.staticTexts["history.explanation"].exists)
        XCTAssertFalse(app.staticTexts["Research entry zone"].exists)
        XCTAssertFalse(app.buttons["Pause"].exists)
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
        try isolatePaperAcceptance(app)
        XCTAssertTrue(desk.path.hasPrefix(try XCTUnwrap(app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"]) + "/"))
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        waitEnabled(app.buttons["paperSession.action"])
        let startedAt = Date()
        let startedUptime = ProcessInfo.processInfo.systemUptime
        app.buttons["paperSession.action"].click()
        waitSession("Pause", app: app)
        let collected = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            let data = (try? Data(contentsOf: observations)) ?? Data()
            return ProcessInfo.processInfo.systemUptime - startedUptime >= Double(minutes) * 60
                && data.filter { $0 == 10 }.count >= baseline + minutes * 2
                && Self.freshSymbols(at: observations, after: before, since: max(startedAt, Date().addingTimeInterval(-90)))
                    .isSuperset(of: ["BTCUSDT", "ETHUSDT"])
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [collected], timeout: Double(minutes + 2) * 60), .completed)
        capture(app, "09-sustained-public-collection")
        app.buttons["paperSession.action"].click()
        waitSession("Start", app: app)
        XCTAssertEqual(try Data(contentsOf: protocolURL), originalProtocol)
        XCTAssertTrue(try Data(contentsOf: observations).starts(with: before))
        XCTAssertEqual(app.buttons["paperSession.action"].label, "Start")
        capture(app, "10-soak-stopped-retained")
    }
}
