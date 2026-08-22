import XCTest

final class NowcasterUITests: XCTestCase {
    func testPrimarySidebarDestinationsAreKeyboardReachable() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-light", "--destination=today"]
        app.launch()
        XCTAssertTrue(app.outlines["sidebar"].waitForExistence(timeout: 5))
        for identifier in ["today", "markets", "earnings", "signals", "backtests", "modelLab", "dataQuality", "pipelineRuns"] {
            XCTAssertTrue(app.descendants(matching: .any)["sidebar.\(identifier)"].exists)
        }
        XCTAssertTrue(app.buttons["toolbar.refresh"].exists)
    }

    func testMonitorTablesExposeStableIdentifiers() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-dark", "--destination=markets"]
        app.launch()
        XCTAssertTrue(app.tables["markets.table"].waitForExistence(timeout: 5))
    }
}
