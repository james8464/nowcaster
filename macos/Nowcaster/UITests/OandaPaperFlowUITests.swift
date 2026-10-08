import XCTest

/// Isolated window-level smoke test. It never loads the real Keychain token.
@MainActor
final class OandaPaperFlowUITests: XCTestCase {
    func testPracticeIndicatorIsVisibleButDoesNotAutoStart() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        app.launchArguments = ["--destination=tradeDesk", "-ApplePersistenceIgnoreState", "YES"]
        app.launch()
        defer { app.terminate() }
        app.activate()
        app.menuBars.menuBarItems["Paper Session"].click()
        app.menuItems["Open Nowcaster"].click()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 30))
        let control = app.buttons["tradeDesk.oandaMonitoring"]
        XCTAssertTrue(control.waitForExistence(timeout: 30))
        XCTAssertEqual(control.label, "Start Monitoring")
        XCTAssertTrue(app.descendants(matching: .any)["tradeDesk.intradayReport"].exists)
        XCTAssertFalse(app.buttons["Place Order"].exists)
        app.typeKey("w", modifierFlags: .command)
        XCTAssertNotEqual(app.state, .notRunning)
        app.menuBars.menuBarItems["Paper Session"].click()
        app.menuItems["Open Nowcaster"].click()
        XCTAssertTrue(app.buttons["tradeDesk.oandaMonitoring"].waitForExistence(timeout: 20))
    }
}
