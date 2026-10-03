import XCTest

/// Explicit user handoff, not a lifecycle test: attach to the ordinarily opened
/// installed app and intentionally leave it running after normal Start.
@MainActor
final class FinalPaperSessionHandoffUITests: XCTestCase {
    func testOptInStartAlreadyOpenedInstalledSessionAndLeaveRunning() throws {
        let environment = ProcessInfo.processInfo.environment
        try XCTSkipUnless(environment["NOWCASTER_UI_FINAL_HANDOFF"] == "1")
        continueAfterFailure = false
        addUIInterruptionMonitor(withDescription: "User must dismiss system interruption") { _ in
            XCTFail("An external dialog interrupted handoff; no automatic response is permitted.")
            return true
        }
        let expected = try XCTUnwrap(environment["NOWCASTER_UI_EXPECTED_CAMPAIGN_HASH"])
        XCTAssertEqual(expected.count, 64)
        XCTAssertTrue(expected.allSatisfy { "0123456789abcdef".contains($0) })
        let root = URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_REAL_ROOT"]))
        XCTAssertEqual(root.lastPathComponent, "Nowcaster")
        XCTAssertEqual(root.deletingLastPathComponent().lastPathComponent, "Application Support")
        let preferencesURL = root.appending(path: "paper-session.json")
        func json(_ url: URL) throws -> [String: Any] {
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        }
        let before = try json(preferencesURL)
        XCTAssertEqual(before["campaignHash"] as? String, expected)
        XCTAssertEqual(before["learningEnabled"] as? Bool, true)
        XCTAssertEqual(before["resumeOnLaunch"] as? Bool, false)
        let observation = try json(URL(fileURLWithPath: try XCTUnwrap(environment["NOWCASTER_UI_PROCESS_SNAPSHOT"])))
        let observed = try XCTUnwrap(observation["observed_at"] as? Double)
        XCTAssertLessThan(abs(Date().timeIntervalSince1970 - observed), 3, "A fresh external observation is required.")
        let processes = try XCTUnwrap(observation["processes"] as? [String])
        XCTAssertFalse(processes.contains { $0.contains("/Applications/Nowcaster.app/Contents/Helpers/") },
                       "Do not start over an existing installed helper session.")

        let app = XCUIApplication(url: URL(fileURLWithPath: "/Applications/Nowcaster.app"))
        XCTAssertTrue([XCUIApplication.State.runningForeground, .runningBackground].contains(app.state),
                      "The user-owned installed app must already be ordinarily launched; this action never launches it.")
        app.activate()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 15))
        let action = app.buttons["paperSession.action"]
        XCTAssertTrue(action.waitForExistence(timeout: 15))
        XCTAssertTrue(action.isEnabled)
        XCTAssertEqual(action.label, "Start")
        let status = app.staticTexts["paperSession.status"]
        XCTAssertEqual(status.value as? String ?? status.label, "Not started")
        action.click()
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            let value = status.value as? String ?? status.label
            return value == "Needs attention" || (action.isEnabled && action.label == "Pause")
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 180), .completed)
        XCTAssertNotEqual(status.value as? String ?? status.label, "Needs attention", app.windows.firstMatch.debugDescription)
        XCTAssertEqual(action.label, "Pause")
        XCTAssertTrue(action.isEnabled)
        XCTAssertNotEqual(app.state, .notRunning)
        let after = try json(preferencesURL)
        for key in ["campaignID", "campaignHash", "runtimeCodeIdentity", "manifestURL", "createdAt"] {
            XCTAssertEqual(try XCTUnwrap(after[key] as? String), try XCTUnwrap(before[key] as? String), "Active pin changed: " + key)
        }
        for key in ["learningEnabled", "resumeOnLaunch", "showMenuBarExtra"] {
            XCTAssertEqual(try XCTUnwrap(after[key] as? Bool), try XCTUnwrap(before[key] as? Bool), "Opt-in changed: " + key)
        }
        let tree = XCTAttachment(string: app.windows.firstMatch.debugDescription)
        tree.name = "final-handoff-running-accessibility"; tree.lifetime = .keepAlways; add(tree)
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "final-handoff-running"; screenshot.lifetime = .keepAlways; add(screenshot)
        print("FINAL_HANDOFF_STARTED campaign=\(expected) status=\(status.value as? String ?? status.label)")
        // Deliberately no launch, preference writes, helper command or teardown:
        // the controller independently verifies children and fresh receipts next.
    }
}
