import Foundation
import Testing
@testable import NowcasterApp

private let workflowProtocol = String(repeating: "a", count: 64)
private let workflowPolicy = String(repeating: "b", count: 64)
private let workflowSource = String(repeating: "c", count: 64)
private let workflowTime = "2026-10-04T12:00:00Z"
private let workflowNow = ISO8601DateFormatter().date(from: workflowTime)!

private func workflowPayload() -> [String: Any] {
    let empty = NSNull()
    var account: [String: Any] = ["schemaVersion": 1, "policyHash": workflowPolicy,
        "protocolHash": workflowProtocol, "sourceIdentityHash": workflowSource,
        "activatedAt": workflowTime, "lastAt": workflowTime, "utcDay": "2026-10-04",
        "cash": "10000", "equity": "10000", "peakEquity": "10000", "valuationAt": empty,
        "cooldownUntil": empty, "pendingEntry": empty, "pendingExit": empty,
        "lastObservationHash": empty, "lastConsumedQuoteKey": empty,
        "lastConsumedQuoteAt": empty, "lastConsumedQuoteQuantity": empty]
    for key in ["unrealizedPnl", "realizedPnl", "realizedLosses", "fees", "slippageCost", "maximumDrawdown", "dailyLoss"] { account[key] = "0" }
    for key in ["dailyEntries", "consecutiveLosses", "totalEntries", "completedTrades", "totalWins", "totalLosses"] { account[key] = 0 }
    return ["schemaVersion": 1, "paperOnly": true, "protocolHash": workflowProtocol,
        "policyHash": workflowPolicy, "updatedAt": workflowTime, "state": "watching", "reasons": [],
        "decisions": [], "account": account, "positions": [], "recentTrades": [],
        "review": ["completedTrades": 0, "wins": 0, "losses": 0, "netPnl": "0", "fees": "0", "maximumDrawdown": "0", "setups": []]]
}

private func workflowData(_ payload: [String: Any]) throws -> Data {
    try JSONSerialization.data(withJSONObject: payload)
}

private func workflowDecision() -> [String: Any] {
    ["symbol": "BTCUSDT", "status": "ready", "reasons": [], "decisionAt": "2026-10-04T11:59:59Z",
     "expiresAt": "2026-10-04T12:00:59Z", "protocolHash": workflowProtocol, "policyHash": workflowPolicy,
     "sourceIdentityHash": workflowSource, "sourceKey": "observed-bar", "observationAt": "2026-10-04T11:59:58Z",
     "observationHash": workflowProtocol, "contextHash": workflowPolicy, "session": "europe", "calendarAvailable": true,
     "rankScore": "2", "setup": "breakout", "triggerLevel": "100", "entry": "101", "stop": "99", "target": "105"]
}

private func workflowPosition() -> [String: Any] {
    ["origin": workflowDecision(), "entryAt": workflowTime, "entrySourceKey": "later-bar", "entryQuoteKey": "later-quote",
     "initialQuantity": "1", "quantity": "1", "entryPrice": "101", "entryFee": "0.1", "entrySlippage": "0.05",
     "unitDebit": "101.1", "initialRisk": "2.1", "stop": "99", "stopEffectiveAt": workflowTime, "target": "105", "realizedPnl": "0",
     "exitNotional": "0", "exitFees": "0", "exitSlippage": "0"]
}

@Suite struct DiagnosticWorkflowTests {
    @Test func positionStopTimestampRequiresCausalEntryAndAccountBounds() throws {
        var payload = workflowPayload(), account = payload["account"] as! [String: Any], position = workflowPosition()
        payload["state"] = "position_open"
        account["lastAt"] = "2026-10-04T12:00:05Z"; payload["account"] = account
        let now = workflowNow.addingTimeInterval(5)
        for stamp in [workflowTime, "2026-10-04T12:00:05Z"] {
            position["stopEffectiveAt"] = stamp; payload["positions"] = [position]
            let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: now)
            #expect(value.positions.first?.string("stopEffectiveAt") == stamp)
        }
        for invalid: Any in [NSNull(), "bad", "2026-10-04T12:00:00+00:00", "2026-10-04T11:59:59Z", "2026-10-04T12:00:06Z"] {
            position["stopEffectiveAt"] = invalid; payload["positions"] = [position]
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: now) }
        }
        account["lastAt"] = workflowTime; payload["account"] = account
        position["stopEffectiveAt"] = "2026-10-04T12:00:01Z"; payload["positions"] = [position]
        #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: now) }
        position.removeValue(forKey: "stopEffectiveAt"); payload["positions"] = [position]
        #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: now) }
    }

    @Test func decodesActualCamelCaseDecimalProjectionWithoutInventingValuation() throws {
        let value = try DiagnosticWorkflow.decode(workflowData(workflowPayload()), protocolHash: workflowProtocol, now: workflowNow)
        #expect(value.account?.string("cash") == "10000")
        #expect(value.account?.date("valuationAt") == nil)
        #expect(value.state == "watching")
    }

    @Test func rejectsWrongIdentitySchemaPaperOnlyAndMalformedStates() throws {
        let mutations: [(String, Any)] = [("protocolHash", workflowPolicy), ("policyHash", "bad"),
            ("paperOnly", false), ("schemaVersion", 2), ("schemaVersion", true), ("state", "buy"),
            ("updatedAt", "2026-10-04T12:00:01Z"), ("updatedAt", "2026-10-04T12:00:00+00:00"),
            ("positions", [[:], [:]]), ("order", "real")]
        for (key, bad) in mutations {
            var payload = workflowPayload(); payload[key] = bad
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
        }
    }

    @Test func rejectsInvalidAccountDecimalCountsChronologyAndNestedIdentity() throws {
        let mutations: [(String, Any)] = [("cash", "NaN"), ("equity", "Infinity"), ("cash", "-1"),
            ("cash", 10000), ("fees", "-1"), ("dailyEntries", -1), ("completedTrades", 0.5),
            ("maximumDrawdown", "1.1"), ("lastAt", "2026-10-04T12:01:00Z"),
            ("activatedAt", "2026-10-04T12:00:01Z"), ("valuationAt", "2026-10-04T12:00:01Z"),
            ("lastObservationHash", "bad"), ("protocolHash", workflowPolicy), ("policyHash", workflowProtocol),
            ("lastConsumedQuoteQuantity", "0"), ("unexpected", "x")]
        for (key, bad) in mutations {
            var payload = workflowPayload(); var account = payload["account"] as! [String: Any]
            account[key] = bad; payload["account"] = account
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
        }
    }

    @Test func disabledAndErrorNeverCarryAnOldAccount() throws {
        for state in ["disabled", "error"] {
            var payload = workflowPayload(); payload["state"] = state
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
            for key in ["account", "review", "updatedAt"] { payload[key] = NSNull() }
            let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
            #expect(value.account == nil)
        }
    }

    @Test func preservesPendingOpenAndPendingExitWithoutClaimingAnOrder() throws {
        for state in ["pending_entry", "position_open", "pending_exit", "stale", "limited"] {
            var payload = workflowPayload(), account = payload["account"] as! [String: Any]
            payload["state"] = state
            if state == "pending_entry" { account["pendingEntry"] = workflowDecision() }
            if ["position_open", "pending_exit", "stale"].contains(state) {
                payload["positions"] = [workflowPosition()]
                account["valuationAt"] = workflowTime
            }
            if state == "pending_exit" {
                account["pendingExit"] = ["reason": "invalidation", "triggeredAt": workflowTime, "sourceKey": NSNull()]
            }
            payload["account"] = account; payload["decisions"] = [workflowDecision()]
            let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
            #expect(value.state == state)
            #expect(value.positions.first?.fields("origin")?.string("symbol") == (value.positions.isEmpty ? nil : "BTCUSDT"))
        }
    }

    @Test func rejectsTamperedPositionDecisionAndReviewProjection() throws {
        for key in ["origin", "quantity", "entryAt", "target", "initialRisk"] {
            var payload = workflowPayload(), position = workflowPosition()
            payload["state"] = "position_open"
            position[key] = key == "origin" ? ["symbol": "SHORT"] : key == "entryAt" ? "2026-10-04T12:00:01Z" : "0"
            payload["positions"] = [position]
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
        }
        for (key, bad) in [("stop", "102"), ("protocolHash", workflowPolicy), ("sourceIdentityHash", workflowPolicy), ("expiresAt", "2026-10-04T12:02:00Z")] {
            var payload = workflowPayload(), decision = workflowDecision()
            decision[key] = bad; payload["decisions"] = [decision]
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
        }
        var payload = workflowPayload(), review = payload["review"] as! [String: Any]
        review["netPnl"] = "900"; payload["review"] = review
        #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
    }

    @Test @MainActor func serviceLoadsEnablesAndClearsInvalidOrChangedSource() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let scripts = root.appending(path: "scripts")
        try FileManager.default.createDirectory(at: scripts, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("{}".utf8).write(to: root.appending(path: "protocol.json"))
        try """
        case "$1" in
          status) printf '%s\\n' '{"kind":"stopped","protocol_hash":"\(workflowProtocol)","updated_at":"2020-01-01T00:00:00Z","evaluated_at":null,"reasons":[],"suggestion":null}' ;;
          workflow-status) cat "$3/workflow.json" ;;
          workflow-enable) touch "$3/explicit-enable"; if [ -f "$3/delay" ]; then touch "$3/inflight-enable"; exec /usr/bin/python3 -c 'import time; time.sleep(20)'; fi; cat "$3/workflow.json" ;;
          *) exit 99 ;;
        esac
        """.write(to: scripts.appending(path: "run_live_paper_signals.py"), atomically: true, encoding: .utf8)
        let path = root.appending(path: "workflow.json")
        try workflowData(workflowPayload()).write(to: path)
        let service = LivePaperSignalService()
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.workflow?.account?.string("cash") == "10000")
        #expect(!FileManager.default.fileExists(atPath: root.appending(path: "explicit-enable").path))
        await service.enableWorkflow()
        #expect(FileManager.default.fileExists(atPath: root.appending(path: "explicit-enable").path))
        #expect(service.workflow?.state == "watching")
        try Data("{}".utf8).write(to: path)
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.workflow == nil)
        #expect(service.workflowMessage != nil)
        try workflowData(workflowPayload()).write(to: path)
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.workflow != nil)
        let retainedSource = service.selectedSource
        await service.open(directory: root.appending(path: "missing"), sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.workflow == nil)
        #expect(service.selectedSource == retainedSource)
        #expect(service.directory == root)
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        try Data().write(to: root.appending(path: "delay"))
        let enabling = Task { await service.enableWorkflow() }
        let deadline = ContinuousClock.now + .seconds(5)
        while !FileManager.default.fileExists(atPath: root.appending(path: "inflight-enable").path), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(FileManager.default.fileExists(atPath: root.appending(path: "inflight-enable").path))
        // An uncooperative CLI is force-stopped at the ownership deadline;
        // false honestly reports escalation rather than a graceful drain.
        #expect(!(await service.shutdown(timeout: .milliseconds(200))))
        await enabling.value
        #expect(service.workflow == nil)
        #expect(!service.workflowBusy)
        #expect(service.pendingOperationCount == 0)
        #expect(!service.notificationsEnabled)
        try FileManager.default.removeItem(at: root.appending(path: "delay"))
        await service.enableWorkflow()
        #expect(service.workflow?.state == "watching")
        #expect(!service.isRunning)
        #expect(!service.notificationsEnabled)
    }

    @Test func screenExpiresSetupsAndMarksMoneyAsHistoricalWithoutPolling() throws {
        var payload = workflowPayload(), account = payload["account"] as! [String: Any]
        payload["decisions"] = [workflowDecision()]; account["valuationAt"] = workflowTime
        payload["account"] = account
        let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        let current = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: true, now: workflowNow)
        #expect(current.title == "Watching")
        #expect(current.currentSetups.count == 1)
        #expect(!current.historical)
        let expired = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: true, now: workflowNow.addingTimeInterval(15))
        #expect(expired.title == "Stale source")
        #expect(expired.currentSetups.isEmpty)
        #expect(expired.historical)
        #expect(expired.valuation.contains("Stale"))
        let paused = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: false, now: workflowNow)
        #expect(paused.title == "Paused · retained simulation")
        #expect(paused.currentSetups.isEmpty)
        #expect(paused.historical)
        #expect(DiagnosticWorkflowPresentation.make(nil, message: "Failed", isRunning: true, now: workflowNow).title == "Unavailable")
    }

    @Test func closedTradeReviewRetainsNetLossAndRejectsFutureOrInvalidOutcome() throws {
        var payload = workflowPayload(), account = payload["account"] as! [String: Any]
        account["totalEntries"] = 1; account["completedTrades"] = 1; account["totalLosses"] = 1
        account["realizedPnl"] = "-2"; account["fees"] = "0.2"
        payload["account"] = account
        let setup: [String: Any] = ["setup": "breakout", "completed": 1, "wins": 0, "losses": 1, "netPnl": "-2", "fees": "0.2"]
        payload["review"] = ["completedTrades": 1, "wins": 0, "losses": 1, "netPnl": "-2", "fees": "0.2", "maximumDrawdown": "0", "setups": [setup]]
        let outcome: [String: Any] = ["decisionId": workflowProtocol, "symbol": "BTCUSDT", "setup": "breakout",
            "entryAt": "2026-10-04T11:59:00Z", "exitAt": workflowTime, "entrySourceKey": "entry-bar", "exitSourceKey": "exit-bar",
            "quantity": "1", "entryPrice": "101", "exitPrice": "99.2", "fees": "0.2", "slippageCost": "0.1",
            "netPnl": "-2", "netReturn": "-0.0198", "reason": "invalidation"]
        payload["recentTrades"] = [outcome]
        let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        #expect(value.review?.count("losses") == 1)
        #expect(value.recentTrades.first?.string("netPnl") == "-2")
        let presentation = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: true, now: workflowNow)
        #expect(presentation.completedPnl == -2)
        #expect(presentation.openPositionRealizedPnl == nil)
        for (key, bad) in [("exitAt", "2026-10-04T12:00:01Z"), ("fees", "-1"), ("quantity", "0"), ("symbol", "SHORT"), ("netReturn", "NaN")] {
            var invalid = outcome; invalid[key] = bad; payload["recentTrades"] = [invalid]
            #expect(throws: (any Error).self) { try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow) }
        }
    }

    @Test func rejectsPolicyChangeAgainstPreviouslySelectedIdentity() throws {
        #expect(throws: (any Error).self) {
            try DiagnosticWorkflow.decode(workflowData(workflowPayload()), protocolHash: workflowProtocol, policyHash: workflowSource, now: workflowNow)
        }
    }

    @Test func partialExitDoesNotBecomeCompletedTradePnl() throws {
        var payload = workflowPayload(), account = payload["account"] as! [String: Any], position = workflowPosition()
        payload["state"] = "position_open"; account["totalEntries"] = 1
        account["realizedPnl"] = "2"; account["fees"] = "0.2"
        position["quantity"] = "0.5"; position["realizedPnl"] = "2"
        position["exitNotional"] = "52"; position["exitFees"] = "0.1"; position["exitSlippage"] = "0.05"
        payload["account"] = account; payload["positions"] = [position]
        var review = payload["review"] as! [String: Any]
        review["netPnl"] = "2"; review["fees"] = "0.2"; payload["review"] = review
        let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        let presentation = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: true, now: workflowNow)
        #expect(value.review?.count("completedTrades") == 0)
        #expect(presentation.completedPnl == 0)
        #expect(presentation.openPositionRealizedPnl == 2)
        // Earlier completed losses coexist with a partial gain in the open
        // position. The account/review lifetime realized total is their sum.
        account["totalEntries"] = 2; account["completedTrades"] = 1; account["totalLosses"] = 1
        position["realizedPnl"] = "5"; payload["account"] = account; payload["positions"] = [position]
        review["completedTrades"] = 1; review["losses"] = 1
        review["setups"] = [["setup": "breakout", "completed": 1, "wins": 0, "losses": 1, "netPnl": "-3", "fees": "0.1"]]
        payload["review"] = review
        let mixed = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        let mixedPresentation = DiagnosticWorkflowPresentation.make(mixed, message: nil, isRunning: true, now: workflowNow)
        #expect(mixedPresentation.completedPnl == -3)
        #expect(mixedPresentation.openPositionRealizedPnl == 5)
    }

    @Test func backendEvidenceErrorExposesReasonAndInvestigationRecovery() throws {
        var payload = workflowPayload(); payload["state"] = "error"
        for key in ["account", "review", "updatedAt"] { payload[key] = NSNull() }
        payload["reasons"] = ["workflow_evidence_unavailable"]
        let value = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        let presentation = DiagnosticWorkflowPresentation.make(value, message: nil, isRunning: false, now: workflowNow)
        #expect(presentation.backendErrorReasons == ["workflow_evidence_unavailable"])
        #expect(presentation.recovery == .investigateAndReload)
        #expect(presentation.completedPnl == nil)
        payload["state"] = "disabled"; payload["reasons"] = []
        let disabled = try DiagnosticWorkflow.decode(workflowData(payload), protocolHash: workflowProtocol, now: workflowNow)
        #expect(DiagnosticWorkflowPresentation.make(disabled, message: nil, isRunning: false, now: workflowNow).recovery == .chooseAndEnable)
    }
}
