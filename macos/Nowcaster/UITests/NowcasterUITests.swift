import XCTest

@MainActor
func isolatePaperAcceptance(_ app: XCUIApplication) throws {
    let supplied = ProcessInfo.processInfo.environment["NOWCASTER_UI_STORAGE_ROOT"]
    let root = supplied.map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.temporaryDirectory.appending(path: "UIAcceptanceFixtures/\(UUID().uuidString)")
    if supplied != nil {
        // The sandboxed runner can read a caller-prepared root but cannot rewrite
        // its marker in /tmp. The app independently validates this same root.
        guard FileManager.default.fileExists(atPath: root.appending(path: "UI-TEST-ONLY.md").path) else {
            throw NSError(domain: "NowcasterUIAcceptance", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Prepare a marked UI acceptance root before running tests."])
        }
    } else {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data("UI TEST ONLY — isolated preferences and paper research".utf8).write(to: root.appending(path: "UI-TEST-ONLY.md"))
    }
    app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"] = root.path
}

@MainActor
final class NowcasterUITests: XCTestCase {
    private func capture(_ app: XCUIApplication, _ name: String) {
        app.activate()
        let tree = XCTAttachment(string: app.windows.firstMatch.debugDescription)
        tree.name = name + "-accessibility"
        tree.lifetime = .keepAlways
        add(tree)
        let image = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        image.name = name
        image.lifetime = .keepAlways
        add(image)
    }

    func testSimpleDeskPrimaryRoutesAndSecondarySetup() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--destination=tradeDesk", "--ui-light"]
        try isolatePaperAcceptance(app)
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.staticTexts["sidebar.history"].waitForExistence(timeout: 30))
        for id in ["tradeDesk", "markets", "history", "research"] { XCTAssertTrue(app.staticTexts["sidebar." + id].exists) }
        XCTAssertFalse(app.staticTexts["sidebar.today"].exists)
        XCTAssertTrue(app.buttons["paperSession.action"].exists)
        app.buttons["tradeDesk.asset.BTCUSDT"].click()
        XCTAssertTrue(app.staticTexts["tradeDesk.detail.title"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["Research entry zone"].exists)
        app.buttons["tradeDesk.closeDetail"].click()
        app.buttons["tradeDesk.setup"].click()
        XCTAssertTrue(app.buttons["paperSignals.setup"].waitForExistence(timeout: 10))
        capture(app, "task5-setup")
        app.typeKey(.escape, modifierFlags: [])
        app.staticTexts["sidebar.history"].click()
        XCTAssertTrue(app.staticTexts["history.explanation"].waitForExistence(timeout: 10))
        capture(app, "task5-history")
        app.staticTexts["sidebar.research"].click()
        XCTAssertTrue(app.staticTexts["research.resources"].waitForExistence(timeout: 10))
        capture(app, "task5-research")
        app.buttons["sidebar.advanced"].click()
        XCTAssertTrue(app.staticTexts["sidebar.today"].waitForExistence(timeout: 10))
    }

    func testPaperSettingsAreIndependentAndMenuIsOptional() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "-ApplePersistenceIgnoreState", "YES"]
        app.launch()
        app.activate()
        defer { app.terminate() }
        // Also exercise the supported reopening route after a previous window
        // was closed; XCTest can launch with no restored main window.
        app.menuBars.menuBarItems["Paper Session"].click()
        app.menuItems["Open Nowcaster"].click()
        XCTAssertTrue(app.buttons["paperSession.action"].waitForExistence(timeout: 30))
        app.activate()
        app.typeKey(",", modifierFlags: .command)
        // Grouped settings render as switches on newer macOS releases.
        func toggleValue(_ element: XCUIElement) -> String { String(describing: element.value ?? "missing") }
        let learning = app.descendants(matching: .any)["settings.learning"]
        XCTAssertTrue(learning.waitForExistence(timeout: 10))
        XCTAssertEqual(toggleValue(learning), "0")
        learning.click()
        XCTAssertEqual(toggleValue(learning), "1")
        XCTAssertEqual(toggleValue(app.descendants(matching: .any)["settings.resume"]), "0")
        XCTAssertEqual(toggleValue(app.descendants(matching: .any)["settings.login"]), "0")
        XCTAssertEqual(toggleValue(app.descendants(matching: .any)["settings.notifications"]), "0")
        let menu = app.descendants(matching: .any)["settings.menu"]
        XCTAssertEqual(toggleValue(menu), "0")
        capture(app, "task5-settings")
        menu.click()
        XCTAssertEqual(toggleValue(menu), "1")
        XCTAssertFalse(app.staticTexts["Broker Credentials"].exists)
        menu.click()
        XCTAssertEqual(toggleValue(menu), "0")
        app.typeKey("w", modifierFlags: .command)
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
        app.launch()
        app.activate()
        app.typeKey(",", modifierFlags: .command)
        XCTAssertTrue(menu.waitForExistence(timeout: 10))
        XCTAssertEqual(toggleValue(menu), "0")
    }
    func testPrimarySidebarDestinationsOpenWithoutStartingMonitoring() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--ui-light", "--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.outlines["sidebar"].waitForExistence(timeout: 30))
        app.buttons["sidebar.advanced"].click()
        for (identifier, title) in [("markets", "Markets"), ("earnings", "Earnings"), ("signals", "Signals"),
                                    ("liveMonitor", "Live Monitor"), ("backtests", "Backtests"),
                                    ("strategyLab", "Strategy Lab"), ("modelLab", "Model Lab"),
                                    ("dataQuality", "Data Quality"), ("pipelineRuns", "Pipeline Runs"),
                                    ("executionCenter", "Execution Center"), ("tradeDesk", "Trade Desk"), ("today", "Today")] {
            let destination = app.staticTexts["sidebar.\(identifier)"]
            XCTAssertTrue(destination.exists)
            destination.click()
            XCTAssertTrue(app.windows[title].waitForExistence(timeout: 30), "Failed navigation: \(title)")
            // The shared command is present on every route; checking a removed
            // view identifier could never detect an accidental session start.
            app.menuBars.menuBarItems["Paper Session"].click()
            XCTAssertTrue(app.menuItems["Start Paper Session"].exists)
            XCTAssertFalse(app.menuItems["Pause Paper Session"].exists)
            app.typeKey(.escape, modifierFlags: [])
            let screenshot = XCTAttachment(screenshot: app.windows[title].screenshot())
            screenshot.name = "navigation-" + identifier
            screenshot.lifetime = .keepAlways
            add(screenshot)
        }
        XCTAssertTrue(app.buttons["toolbar.refresh"].exists)
    }

    func testMonitorTablesExposeStableIdentifiers() throws {
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--ui-dark", "--destination=markets"]
        app.launch()
        defer { app.terminate() }
        // SwiftUI Table is exposed as an AXOutline on current macOS.
        XCTAssertTrue(app.outlines["markets.table"].waitForExistence(timeout: 30))
    }

    func testDeskAppearanceMatrixAndLargeText() throws {
        continueAfterFailure = false
        for appearance in ["light", "dark"] {
            for size in ["wide", "narrow", "minimum"] {
                let app = XCUIApplication()
                try isolatePaperAcceptance(app)
                app.launchArguments = ["--destination=tradeDesk", "--ui-" + appearance, "--ui-" + size]
                app.launch()
                XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].waitForExistence(timeout: 30))
                XCTAssertTrue(app.buttons["paperSession.action"].isHittable)
                XCTAssertTrue(app.staticTexts["paperSession.status"].isHittable)
                let expectedSize: CGSize = size == "wide" ? CGSize(width: 1440, height: 900)
                    : size == "narrow" ? CGSize(width: 900, height: 700) : CGSize(width: 820, height: 620)
                let geometryReady = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
                    let frame = app.windows.firstMatch.frame
                    return abs(frame.width - expectedSize.width) < 1 && abs(frame.height - expectedSize.height) < 1
                }, object: nil)
                XCTAssertEqual(XCTWaiter.wait(for: [geometryReady], timeout: 10), .completed)
                capture(app, "task5-desk-\(appearance)-\(size)")
                app.buttons["tradeDesk.asset.BTCUSDT"].click()
                XCTAssertTrue(app.staticTexts["tradeDesk.detail.title"].waitForExistence(timeout: 10))
                XCTAssertFalse(app.staticTexts["Research entry zone"].exists)
                capture(app, "task5-detail-\(appearance)-\(size)")
                app.typeKey("q", modifierFlags: .command)
                XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
            }
        }
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "--ui-minimum", "--ui-dark"]
        app.launch()
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].waitForExistence(timeout: 30))
        for _ in 0..<7 { app.typeKey("+", modifierFlags: .command) }
        XCTAssertTrue(app.buttons["paperSession.action"].isHittable)
        let larger = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        larger.name = "task5-desk-200percent-minimum"
        larger.lifetime = .keepAlways
        add(larger)
        app.typeKey(",", modifierFlags: .command)
        XCTAssertTrue(app.popUpButtons["settings.textSize"].waitForExistence(timeout: 10))
        XCTAssertEqual(String(describing: app.popUpButtons["settings.textSize"].value ?? "missing"), "200%")
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
    }

    func testKeyboardSidebarNavigationAndSettingsShortcut() throws {
        // Catches broken native sidebar key navigation or lost Settings access.
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "--ui-minimum"]
        app.launch()
        defer { app.terminate() }
        let geometryReady = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            app.windows.firstMatch.frame.size == CGSize(width: 820, height: 620)
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [geometryReady], timeout: 10), .completed)
        app.activate()
        XCTAssertTrue(app.staticTexts["sidebar.tradeDesk"].waitForExistence(timeout: 30))
        app.staticTexts["sidebar.tradeDesk"].click()
        for title in ["Markets", "History", "Research"] {
            app.typeKey(.downArrow, modifierFlags: [])
            XCTAssertTrue(app.windows[title].waitForExistence(timeout: 10))
        }
        capture(app, "task5-keyboard-research")
        app.typeKey(",", modifierFlags: .command)
        XCTAssertTrue(app.descendants(matching: .any)["settings.learning"].waitForExistence(timeout: 10))
        app.typeKey("w", modifierFlags: .command)
        XCTAssertTrue(app.windows["Research"].exists)
    }

    func testLargeTextCanReachLowerDeskHelp() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "--ui-minimum", "--ui-dark"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].waitForExistence(timeout: 30))
        app.activate()
        for _ in 0..<7 { app.typeKey("+", modifierFlags: .command) }
        let help = app.buttons["About This Research…"]
        let workspace = app.scrollViews["tradeDesk.workspace"]
        for _ in 0..<8 {
            if help.isHittable { break }
            workspace.scroll(byDeltaX: 0, deltaY: -180)
        }
        XCTAssertTrue(help.isHittable)
        XCTAssertTrue(app.buttons["tradeDesk.asset.ETHUSDT"].isHittable)
        capture(app, "task5-200percent-lower-content")
        help.click()
        XCTAssertTrue(app.sheets.firstMatch.waitForExistence(timeout: 10))
        capture(app, "task5-200percent-help")
        let helpContent = app.scrollViews["paperResearch.helpContent"]
        XCTAssertTrue(helpContent.exists)
        let finalParagraph = app.staticTexts["paperResearch.limitations"].firstMatch
        XCTAssertTrue(finalParagraph.exists)
        for _ in 0..<8 {
            if helpContent.frame.contains(finalParagraph.frame) { break }
            helpContent.scroll(byDeltaX: 0, deltaY: -180)
        }
        capture(app, "task5-200percent-help-bottom")
        XCTAssertTrue(helpContent.frame.contains(finalParagraph.frame))
        XCTAssertTrue(app.buttons["Close"].isHittable)
        app.buttons["Close"].click()
        XCTAssertFalse(app.sheets.firstMatch.exists)
    }

    func testWideDetailNormalPaintEvidence() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "--ui-wide", "--ui-light"]
        app.launch()
        defer { app.terminate() }
        let geometryReady = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            app.windows.firstMatch.frame.size == CGSize(width: 1440, height: 900)
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [geometryReady], timeout: 10), .completed)
        XCTAssertTrue(app.buttons["tradeDesk.asset.BTCUSDT"].waitForExistence(timeout: 30))
        app.activate()
        app.buttons["tradeDesk.asset.BTCUSDT"].click()
        XCTAssertTrue(app.staticTexts["tradeDesk.detail.title"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["paperSession.status"].isHittable)
        XCTAssertTrue(app.buttons["tradeDesk.asset.ETHUSDT"].isHittable)
        capture(app, "task5-wide-detail-fresh")
        capture(app, "task5-wide-detail-second")
        app.buttons["tradeDesk.asset.ETHUSDT"].click()
        capture(app, "task5-wide-after-eth-click")
        let selectedDetail = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            String(describing: app.staticTexts["tradeDesk.detail.title"].value ?? "missing") == "ETHUSDT"
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [selectedDetail], timeout: 5), .completed)
        capture(app, "task5-wide-detail-eth")
    }

    func testMinimumBacktestsKeepsSidebarAndInspectorReachable() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=backtests", "--ui-minimum", "--ui-light"]
        app.launch()
        defer { app.terminate() }
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            app.windows["Backtests"].exists && app.windows.firstMatch.frame.size == CGSize(width: 820, height: 620)
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 15), .completed)
        app.activate()
        let nextRoute = app.staticTexts["sidebar.strategyLab"]
        if !nextRoute.exists { app.buttons["sidebar.advanced"].click() }
        capture(app, "task5-backtests-minimum-before-navigation")
        XCTAssertTrue(nextRoute.isHittable)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(nextRoute.frame))
        let inspector = app.scrollViews["inspector.horizontalViewport"]
        XCTAssertTrue(inspector.exists)
        capture(app, "task5-backtests-minimum")
        // The outer viewport has no exposed hit point because its content is
        // another scroll view. Dispatch the gesture over visible chart content.
        app.staticTexts["Chart"].firstMatch.scroll(byDeltaX: -220, deltaY: 0)
        capture(app, "task5-backtests-minimum-scrolled")
        nextRoute.click()
        XCTAssertTrue(app.windows["Strategy Lab"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["sidebar.tradeDesk"].isHittable)
        capture(app, "task5-strategy-minimum")
    }
}
