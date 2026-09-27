import XCTest

@MainActor
final class NowcasterUITests: XCTestCase {
    func testPrimarySidebarDestinationsOpenWithoutStartingMonitoring() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-light", "--destination=today"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.outlines["sidebar"].waitForExistence(timeout: 30))
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
        app.launchArguments = ["--ui-dark", "--destination=markets"]
        app.launch()
        defer { app.terminate() }
        // SwiftUI Table is exposed as an AXOutline on current macOS.
        XCTAssertTrue(app.outlines["markets.table"].waitForExistence(timeout: 30))
    }
}
