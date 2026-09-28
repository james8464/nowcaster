import XCTest

@MainActor
func isolatePaperAcceptance(_ app: XCUIApplication) throws {
    let root = ProcessInfo.processInfo.environment["NOWCASTER_UI_STORAGE_ROOT"].map { URL(fileURLWithPath: $0) }
        ?? URL(fileURLWithPath: "/tmp/UIAcceptanceFixtures/\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try Data("UI TEST ONLY — isolated preferences and paper research".utf8).write(to: root.appending(path: "UI-TEST-ONLY.md"))
    app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"] = root.path
}

@MainActor
final class NowcasterUITests: XCTestCase {
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
        app.typeKey(.escape, modifierFlags: [])
        app.staticTexts["sidebar.history"].click()
        XCTAssertTrue(app.staticTexts["history.explanation"].waitForExistence(timeout: 10))
        app.staticTexts["sidebar.research"].click()
        XCTAssertTrue(app.staticTexts["research.resources"].waitForExistence(timeout: 10))
        app.buttons["sidebar.advanced"].click()
        XCTAssertTrue(app.staticTexts["sidebar.today"].waitForExistence(timeout: 10))
    }

    func testPaperSettingsAreIndependentAndMenuIsOptional() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.buttons["paperSession.action"].waitForExistence(timeout: 30))
        app.typeKey(",", modifierFlags: .command)
        let learning = app.checkBoxes["settings.learning"]
        XCTAssertTrue(learning.waitForExistence(timeout: 10))
        XCTAssertEqual(learning.value as? String, "0")
        learning.click()
        XCTAssertEqual(learning.value as? String, "1")
        XCTAssertEqual(app.checkBoxes["settings.resume"].value as? String, "0")
        XCTAssertEqual(app.checkBoxes["settings.login"].value as? String, "0")
        XCTAssertEqual(app.checkBoxes["settings.notifications"].value as? String, "0")
        let menu = app.checkBoxes["settings.menu"]
        XCTAssertEqual(menu.value as? String, "0")
        menu.click()
        XCTAssertEqual(menu.value as? String, "1")
        XCTAssertFalse(app.staticTexts["Broker Credentials"].exists)
        menu.click()
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
            XCTAssertFalse(app.buttons["paperSignals.stop"].exists)
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
                let attachment = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
                attachment.name = "task5-desk-\(appearance)-\(size)"
                attachment.lifetime = .keepAlways
                add(attachment)
                app.buttons["tradeDesk.asset.BTCUSDT"].click()
                XCTAssertTrue(app.staticTexts["tradeDesk.detail.title"].waitForExistence(timeout: 10))
                XCTAssertFalse(app.staticTexts["Research entry zone"].exists)
                let detail = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
                detail.name = "task5-detail-\(appearance)-\(size)"
                detail.lifetime = .keepAlways
                add(detail)
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
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 30))
    }
}
