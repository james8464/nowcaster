import Foundation
import Testing
@testable import NowcasterApp

private let liveNow = ISO8601DateFormatter().date(from: "2026-09-21T12:00:01Z")!
private let liveHash = String(repeating: "a", count: 64)

private func livePayload() -> [String: Any] {
    ["kind": "published", "protocol_hash": liveHash, "updated_at": "2026-09-21T12:00:00Z",
     "evaluated_at": "2026-09-21T12:00:00Z", "reasons": [], "suggestion": [
        "symbol": "BTCUSDT", "strategy_id": "trend", "round_id": "round", "protocol_hash": liveHash,
        "source_hash": String(repeating: "b", count: 64), "candidate_hash": String(repeating: "c", count: 64),
        "evidence_hash": String(repeating: "d", count: 64), "policy_hash": String(repeating: "e", count: 64),
        "source": "binance:spot", "posture": "long_research", "paper_only": true, "qualification_status": "unqualified",
        "timeframe": "1m / 5m", "decision_at": "2026-09-21T12:00:00Z", "available_at": "2026-09-21T12:00:00Z",
        "expires_at": "2026-09-21T12:00:15Z", "entry_low": "100", "entry_high": "101", "invalidation": "99", "target": "103",
        "reasons": ["trend_aligned", "candidate_confirmed"],
        "close_reasons": ["invalidation_reached", "target_reached", "trend_alignment_lost", "evidence_expired"]]]
}

private func liveDecode(_ payload: [String: Any], now: Date = liveNow) throws -> LivePaperSignalState {
    try LivePaperSignalState.decode(JSONSerialization.data(withJSONObject: payload), protocolHash: liveHash, now: now)
}

@Suite struct LivePaperSignalModelsTests {
    @Test func rejectsActionsFutureAndExpiredPublishedState() throws {
        #expect(try liveDecode(livePayload()).suggestion?.symbol == "BTCUSDT")
        for key in ["order_id", "execution", "account", "quantity", "qualified_alert"] {
            var payload = livePayload(); payload[key] = "unexpected"
            #expect(throws: SnapshotValidationError.self) { try liveDecode(payload) }
        }
        #expect(throws: SnapshotValidationError.self) { try liveDecode(livePayload(), now: liveNow.addingTimeInterval(14)) }
        #expect(throws: SnapshotValidationError.self) { try liveDecode(livePayload(), now: liveNow.addingTimeInterval(-2)) }
        var payload = livePayload(); payload["protocol_hash"] = String(repeating: "f", count: 64)
        #expect(throws: SnapshotValidationError.self) { try liveDecode(payload) }
    }

    @Test func publishedRequiresCausalEvaluationAndNoExclusions() throws {
        for (key, value) in [("evaluated_at", NSNull() as Any), ("evaluated_at", "2026-09-21T11:59:59Z"),
                             ("reasons", ["continuity_warmup"])] {
            var payload = livePayload(); payload[key] = value
            #expect(throws: SnapshotValidationError.self) { try liveDecode(payload) }
        }
        var payload = livePayload(); payload["kind"] = "stopped"
        #expect(throws: SnapshotValidationError.self) { try liveDecode(payload) }
    }

    @Test func presentationExpiresAndRequiresOwnedRunningProcess() throws {
        let state = try liveDecode(livePayload())
        #expect(state.currentSuggestion(now: liveNow, isRunning: true) != nil)
        #expect(state.currentSuggestion(now: liveNow, isRunning: false) == nil)
        #expect(state.currentSuggestion(now: liveNow.addingTimeInterval(15), isRunning: true) == nil)
    }

    @Test func statusExpiresAtSuggestionDeadlineBeforeServiceAgeLimit() throws {
        var payload = livePayload()
        var suggestion = payload["suggestion"] as! [String: Any]
        suggestion["expires_at"] = "2026-09-21T12:00:05Z"
        payload["suggestion"] = suggestion
        let state = try liveDecode(payload)
        let display = LivePaperSignalsPresentation(state: state, isRunning: true, now: liveNow.addingTimeInterval(5))
        #expect(display.suggestion == nil)
        #expect(display.status == "Stale")
    }

    @Test func eventHistoryRejectsTornFutureAndActionRecords() throws {
        let good = "{\"kind\":\"started\",\"at\":\"2026-09-21T12:00:00Z\"}\n"
        #expect(try LivePaperSignalEvent.decodeHistory(Data(good.utf8), now: liveNow).count == 1)
        for bad in [String(good.dropLast()), good.replacingOccurrences(of: "started", with: "order"),
                    good.replacingOccurrences(of: "12:00:00", with: "12:01:00"),
                    good.replacingOccurrences(of: "\"kind\"", with: "\"order_id\":\"x\",\"kind\"")] {
            #expect(throws: SnapshotValidationError.self) { try LivePaperSignalEvent.decodeHistory(Data(bad.utf8), now: liveNow) }
        }
    }

    @Test func notificationRejectsInjectedLanguageAndRechecksExpiry() throws {
        let body = "Paper-only research posture — not a trade instruction. BTCUSDT. Expires 2026-09-21T12:00:15Z."
        var payload: [String: Any] = ["protocol_hash": liveHash, "candidate_hash": String(repeating: "c", count: 64),
            "material_key": String(repeating: "d", count: 64), "symbol": "BTCUSDT", "generated_at": "2026-09-21T12:00:01Z",
            "expires_at": "2026-09-21T12:00:15Z", "cooldown_key": liveHash + ":BTCUSDT", "paper_only": true,
            "destination": "strategy_lab_evidence", "title": "Nowcaster paper research", "body": body]
        let notice = try LivePaperNotification.decode(JSONSerialization.data(withJSONObject: payload), protocolHash: liveHash, now: liveNow)
        #expect(notice.isFresh(at: liveNow))
        #expect(!notice.isFresh(at: liveNow.addingTimeInterval(14)))
        payload["body"] = "Buy BTCUSDT now"
        #expect(throws: SnapshotValidationError.self) {
            try LivePaperNotification.decode(JSONSerialization.data(withJSONObject: payload), protocolHash: liveHash, now: liveNow)
        }
    }

    @Test func invocationIsDedicatedAndEnvironmentHasNoSecrets() throws {
        let config = LivePaperSignalConfiguration(projectRoot: URL(fileURLWithPath: "/tmp/source"),
            executable: URL(fileURLWithPath: "/tmp/python"), script: URL(fileURLWithPath: "/tmp/source/scripts/run_live_paper_signals.py"),
            directory: URL(fileURLWithPath: "/tmp/round"), protocolHash: liveHash)
        #expect(config.arguments(for: "start") == ["-u", "/tmp/source/scripts/run_live_paper_signals.py", "start", "--directory", "/tmp/round"])
        #expect(!LivePaperSignalConfiguration.environment.keys.contains("API_KEY"))
    }

    @Test @MainActor func disablingWhilePermissionIsPendingDoesNotReenableNotifications() async {
        let authorizer = PendingPaperAuthorizer()
        let service = LivePaperSignalService(notifications: authorizer)
        let request = Task { await service.setNotificationsEnabled(true) }
        while authorizer.pending == nil { await Task.yield() }
        await service.setNotificationsEnabled(false)
        authorizer.pending?.resume(returning: true)
        await request.value
        #expect(!service.notificationsEnabled)
    }

    @Test @MainActor func explicitStartAndStopOwnTheActualChild() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        try Data("{}".utf8).write(to: directory.appending(path: "protocol.json"))
        let script = directory.appending(path: "run_live_paper_signals.py")
        let status = "{\"kind\":\"stopped\",\"protocol_hash\":\"\(liveHash)\",\"updated_at\":\"2026-09-21T12:00:00Z\",\"evaluated_at\":null,\"reasons\":[],\"suggestion\":null}"
        try """
        case "$1" in
          start) while [ ! -f "$3/stop" ]; do sleep 0.1; done ;;
          stop) touch "$3/stop"; printf '%s\\n' '\(status)' ;;
          status) printf '%s\\n' '\(status)' ;;
          *) exit 1 ;;
        esac
        """.write(to: script, atomically: true, encoding: .utf8)
        let service = LivePaperSignalService()
        let config = LivePaperSignalConfiguration(projectRoot: directory, executable: URL(fileURLWithPath: "/bin/sh"),
            script: script, directory: directory, protocolHash: liveHash)
        #expect(!service.isRunning)
        await service.start(configuration: config)
        #expect(service.isRunning)
        #expect(!service.notificationsEnabled)
        await service.stop()
        #expect(!service.isRunning)
        #expect(service.state == nil)
    }

    @Test @MainActor func deliveryRechecksRetainedStatusAfterPermissionWait() async throws {
        // The CLI fixture is an external process boundary. The real native
        // controller, strict decoders, permission gate and outcome command run.
        for invalidKind in ["failed", "abstaining", "stale"] {
            let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: directory) }
            try Data("{}".utf8).write(to: directory.appending(path: "protocol.json"))
            let stateURL = directory.appending(path: "status.json")
            var stopped: [String: Any] = ["kind": "stopped", "protocol_hash": liveHash,
                "updated_at": "2020-01-01T00:00:00Z", "evaluated_at": NSNull(), "reasons": [], "suggestion": NSNull()]
            try JSONSerialization.data(withJSONObject: stopped).write(to: stateURL)
            let script = directory.appending(path: "run_live_paper_signals.py")
            try """
            case "$1" in
              start) while [ ! -f "$3/stop" ]; do sleep 0.1; done ;;
              stop) touch "$3/stop"; cat "$3/status.json" ;;
              status) cat "$3/status.json" ;;
              notification) cat "$3/notice.json" ;;
              notification-outcome) printf '%s\\n' "$9" >> "$3/outcomes"; printf '{}\\n' ;;
              *) exit 1 ;;
            esac
            """.write(to: script, atomically: true, encoding: .utf8)
            let delivery = PausedPaperDelivery()
            let service = LivePaperSignalService(notifications: delivery)
            let config = LivePaperSignalConfiguration(projectRoot: directory, executable: URL(fileURLWithPath: "/bin/sh"),
                script: script, directory: directory, protocolHash: liveHash)
            await service.start(configuration: config)
            #expect(service.isRunning)
            let formatter = ISO8601DateFormatter()
            let now = Date(), generated = formatter.string(from: now), expiry = formatter.string(from: now.addingTimeInterval(15))
            var published = livePayload()
            var suggestion = try #require(published["suggestion"] as? [String: Any])
            for key in ["decision_at", "available_at"] { suggestion[key] = generated }
            suggestion["expires_at"] = expiry
            published["suggestion"] = suggestion
            published["updated_at"] = generated; published["evaluated_at"] = generated
            try JSONSerialization.data(withJSONObject: published).write(to: stateURL, options: .atomic)
            let notice: [String: Any] = ["protocol_hash": liveHash, "candidate_hash": String(repeating: "c", count: 64),
                "material_key": String(repeating: "d", count: 64), "symbol": "BTCUSDT", "generated_at": generated,
                "expires_at": expiry, "cooldown_key": liveHash + ":BTCUSDT", "paper_only": true,
                "destination": "strategy_lab_evidence", "title": "Nowcaster paper research",
                "body": "Paper-only research posture — not a trade instruction. BTCUSDT. Expires \(expiry)."]
            try JSONSerialization.data(withJSONObject: notice).write(to: directory.appending(path: "notice.json"))
            await service.setNotificationsEnabled(true)
            for _ in 0 ..< 250 {
                if delivery.pending != nil { break }
                try? await Task.sleep(for: .milliseconds(20))
            }
            #expect(delivery.pending != nil)
            stopped["kind"] = invalidKind; stopped["updated_at"] = generated
            stopped["reasons"] = ["provider_unavailable"]
            try JSONSerialization.data(withJSONObject: stopped).write(to: stateURL, options: .atomic)
            #expect(service.isRunning)
            #expect(Date() < formatter.date(from: expiry)!)
            delivery.pending?.resume(); delivery.pending = nil
            let outcomes = directory.appending(path: "outcomes")
            for _ in 0 ..< 250 {
                if FileManager.default.fileExists(atPath: outcomes.path) { break }
                try? await Task.sleep(for: .milliseconds(20))
            }
            await service.stop()
            #expect(!delivery.scheduled)
            #expect(try String(contentsOf: outcomes, encoding: .utf8) == "failed\n")
        }
    }
}

@MainActor private final class PendingPaperAuthorizer: PaperResearchNotifying {
    var pending: CheckedContinuation<Bool, Never>?
    func requestPaperResearchAuthorization() async -> Bool {
        await withCheckedContinuation { pending = $0 }
    }
    func deliverPaperResearch(_ notice: LivePaperNotification, stillAllowed: @MainActor () async -> Bool) async -> Bool { false }
}

@MainActor private final class PausedPaperDelivery: PaperResearchNotifying {
    var pending: CheckedContinuation<Void, Never>?
    var scheduled = false
    func requestPaperResearchAuthorization() async -> Bool { true }
    func deliverPaperResearch(_ notice: LivePaperNotification, stillAllowed: @MainActor () async -> Bool) async -> Bool {
        await withCheckedContinuation { pending = $0 }
        scheduled = await stillAllowed() && notice.isFresh(at: Date())
        return scheduled
    }
}
