import XCTest

/// Isolated window-level smoke test. It never loads the real Keychain token.
@MainActor
final class OandaPaperFlowUITests: XCTestCase {
    func testInstalledDeskLabelsExperimentalTicketWithoutOrderControl() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        try isolatePaperAcceptance(app)
        let root = try XCTUnwrap(app.launchEnvironment["NOWCASTER_UI_STORAGE_ROOT"])
        let folder = URL(fileURLWithPath: root).appending(path: "IntradayResearch")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let generated = ISO8601DateFormatter().string(from: Date())
        let status = """
        {"schema_version":1,"paper_only":true,"qualification":"unqualified","round_id":"ui-paper-round",
         "cohort_id":"ui-paper-cohort","generated_at":"\(generated)","broker_symbol":"DE30_EUR",
         "feed_health":"healthy","quote_age_seconds":"0","contiguous_bars":4,"gap_count":0,
         "no_trade_reason":"paper_position_open","paper_ticket":{
           "qualification":"unqualified","broker_symbol":"DE30_EUR","direction":"long",
           "entry":"24001","stop":"23950","target":"24103","units":"1",
           "notional_gbp":"20000","effective_leverage":"0.5","margin_estimate_gbp":"1000",
           "estimated_roundtrip_cost_gbp":"5","exit_by":"2026-10-20T08:00:00Z",
           "cost_source":"official practice cost record"}}
        """
        try Data(status.utf8).write(to: folder.appending(path: "experimental-summary.json"))
        app.launchArguments = ["--destination=tradeDesk", "-ApplePersistenceIgnoreState", "YES"]
        app.launch()
        defer { app.terminate() }
        app.activate()
        app.menuBars.menuBarItems["Paper Session"].click()
        app.menuItems["Open Nowcaster"].click()
        XCTAssertTrue(app.staticTexts["Unqualified experimental paper trade"].waitForExistence(timeout: 30))
        let stop = app.staticTexts.matching(NSPredicate(format: "value CONTAINS %@", "Stop 23950")).firstMatch
        XCTAssertTrue(stop.waitForExistence(timeout: 10), app.debugDescription)
        XCTAssertFalse(app.buttons["Place Order"].exists)
    }

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
