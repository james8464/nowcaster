import CryptoKit
import Foundation
import Testing
@testable import NowcasterApp

private let decisionHash = String(repeating: "a", count: 64)
private let contextHash = String(repeating: "b", count: 64)
private let decisionNow = ISO8601DateFormatter().date(from: "2026-09-23T12:00:01Z")!

private func decisionPayload() -> [String: Any] {
    ["schema_version": 1, "paper_only": true, "protocol_hash": decisionHash,
     "context_protocol_hash": contextHash, "generated_at": "2026-09-23T12:00:00Z",
     "contexts": [["symbol": "BTCUSDT", "protocol_hash": decisionHash, "context_protocol_hash": contextHash,
        "report_hash": decisionHash, "feature_hash": contextHash, "decision_at": "2026-09-23T12:00:00Z",
        "available_at": "2026-09-23T12:00:00Z", "expires_at": "2026-09-23T12:00:05Z",
        "regime": "trend", "posture": "long_research", "trends": [1, 5, 15].map {
            ["minutes": $0, "direction": "up", "strength": "0.8"] as [String: Any]
        }, "spread_bps": "2", "volatility_bps": "5", "quote_imbalance": "0.1",
        "session": "europe", "calendar_blackout": false, "reasons": []]],
     "outcomes": [["symbol": "BTCUSDT", "protocol_hash": decisionHash, "context_protocol_hash": contextHash,
        "lifecycle_hash": decisionHash, "record_hash": contextHash, "origin_report_hash": decisionHash,
        "created_at": "2026-09-22T12:00:00Z", "completed_at": "2026-09-22T12:01:00Z",
        "exit_reason": "invalidation", "revision": 1]]]
}

private func decisionData(_ value: [String: Any]) throws -> Data {
    var value = value
    let bytes = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
    value["content_hash"] = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    return try JSONSerialization.data(withJSONObject: value)
}

@Suite struct DayTraderModelsTests {
    @Test func acceptsPythonProjectionWithIndependentCanonicalDigest() throws {
        // Captured from decision_presentation using retained validated reports.
        let json = #"{"schema_version":1,"paper_only":true,"protocol_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","context_protocol_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","generated_at":"2026-09-22T13:00:02+00:00","contexts":[{"symbol":"BTCUSDT","protocol_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","context_protocol_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","report_hash":"3a8d80f7fefce66d2a307c8dd4d2a4b989fda37a75bc4eddc4f5d27de4e57234","feature_hash":"5226f0c5ca533efb6c0717d9054d4b8bd4af5eb498a2127353c215129c738a5e","decision_at":"2026-09-22T13:00:02+00:00","available_at":"2026-09-22T13:00:02+00:00","expires_at":"2026-09-22T13:00:07+00:00","regime":"trend","posture":"long_research","trends":[{"minutes":1,"direction":"up","strength":"1"},{"minutes":5,"direction":"up","strength":"1"},{"minutes":15,"direction":"up","strength":"1"}],"spread_bps":"2","volatility_bps":"2","quote_imbalance":"0.2","session":"europe_americas_overlap","calendar_blackout":false,"reasons":[]}],"outcomes":[],"content_hash":"ec511467d58063ebf663f1d24be9fc024cfa838038873bcbd01a4f8c7491a1d4"}"#
        let now = ISO8601DateFormatter().date(from: "2026-09-22T13:00:03Z")!
        let model = try DayTraderEvidence.decode(Data(json.utf8), protocolHash: decisionHash, now: now)
        #expect(model.currentContexts(now: now, isRunning: true).first?.session == "europe_americas_overlap")
    }

    @Test @MainActor func serviceLoadsCompletedHistoryAndClearsInvalidEvidence() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let scripts = root.appending(path: "scripts")
        try FileManager.default.createDirectory(at: scripts, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("{}".utf8).write(to: root.appending(path: "protocol.json"))
        try """
        case "$1" in
          status) printf '%s\\n' '{"kind":"stopped","protocol_hash":"\(decisionHash)","updated_at":"2020-01-01T00:00:00Z","evaluated_at":null,"reasons":[],"suggestion":null}' ;;
          decision-context) cat "$3/context.json" ;;
          *) exit 99 ;;
        esac
        """.write(to: scripts.appending(path: "run_live_paper_signals.py"), atomically: true, encoding: .utf8)
        var payload = decisionPayload()
        payload["generated_at"] = ISO8601DateFormatter().string(from: Date())
        payload["contexts"] = []
        let path = root.appending(path: "context.json")
        try decisionData(payload).write(to: path)
        let service = LivePaperSignalService()
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.decisionEvidence?.outcomes.count == 1)
        #expect(service.decisionEvidence?.currentContexts(now: Date(), isRunning: service.isRunning).isEmpty == true)
        try Data("{}".utf8).write(to: path)
        await service.open(directory: root, sourceRoot: root, sourcePython: URL(fileURLWithPath: "/bin/sh"))
        #expect(service.decisionEvidence == nil)
        #expect(service.decisionMessage != nil)
    }

    @Test func validatesCurrentContextAndKeepsOutcomesHistorical() throws {
        let model = try DayTraderEvidence.decode(decisionData(decisionPayload()), protocolHash: decisionHash, now: decisionNow)
        #expect(model.currentContexts(now: decisionNow, isRunning: true).count == 1)
        #expect(model.currentContexts(now: decisionNow, isRunning: false).isEmpty)
        #expect(model.currentContexts(now: decisionNow.addingTimeInterval(5), isRunning: true).isEmpty)
        #expect(model.outcomes.first?.exitReason == "invalidation")
        #expect(try #require(model.outcomes.first).completedAt < decisionNow)
    }

    @Test func rejectsExpiredFutureActionAndMismatchedContexts() throws {
        for (key, value) in [("expires_at", "2026-09-23T12:00:01Z"),
                             ("decision_at", "2026-09-23T12:01:00Z"),
                             ("protocol_hash", contextHash), ("posture", "short_research"),
                             ("quantity", "10")] {
            var payload = decisionPayload(), contexts = payload["contexts"] as! [[String: Any]]
            contexts[0][key] = value; payload["contexts"] = contexts
            #expect(throws: SnapshotValidationError.self) {
                try DayTraderEvidence.decode(decisionData(payload), protocolHash: decisionHash, now: decisionNow)
            }
        }
    }

    @Test func rejectsMalformedHistoryAndPayloadTampering() throws {
        for (key, value) in [("completed_at", "2026-09-24T12:00:00Z"),
                             ("exit_reason", "buy"), ("order_id", "123"), ("context_protocol_hash", decisionHash)] {
            var payload = decisionPayload(), outcomes = payload["outcomes"] as! [[String: Any]]
            outcomes[0][key] = value; payload["outcomes"] = outcomes
            #expect(throws: SnapshotValidationError.self) {
                try DayTraderEvidence.decode(decisionData(payload), protocolHash: decisionHash, now: decisionNow)
            }
        }
        var tampered = try JSONSerialization.jsonObject(with: decisionData(decisionPayload())) as! [String: Any]
        tampered["contexts"] = []
        #expect(throws: SnapshotValidationError.self) {
            try DayTraderEvidence.decode(JSONSerialization.data(withJSONObject: tampered), protocolHash: decisionHash, now: decisionNow)
        }
    }
}
