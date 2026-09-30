import AppKit
import Foundation
import Testing
@testable import NowcasterApp

@Suite struct TradeDeskPresentationTests {
    @Test @MainActor func windowMinimumUsesOuterFrameWithoutResizingExistingWindow() {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 750),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let existingFrame = window.frame
        NowcasterWindowPresentation(arguments: []).apply(to: window, initial: true)
        #expect(window.minSize == NSSize(width: 820, height: 620))
        #expect(window.frame == existingFrame)
        let narrow = NowcasterWindowPresentation(arguments: ["--ui-narrow"])
        narrow.apply(to: window, initial: true)
        #expect(window.frame.size == NSSize(width: 900, height: 700))
        window.setFrame(existingFrame, display: false)
        narrow.apply(to: window)
        #expect(window.frame == existingFrame)
    }

    private let now = ISO8601DateFormatter().date(from: "2026-09-20T12:00:00Z")!
    private func publication() throws -> LivePaperSignalState {
        let hash = String(repeating: "a", count: 64)
        let suggestion: [String: Any] = ["symbol": "BTCUSDT", "strategy_id": "trend", "round_id": "round",
            "protocol_hash": hash, "source_hash": hash, "candidate_hash": hash, "evidence_hash": hash,
            "policy_hash": hash, "source": "binance:spot", "posture": "long_research", "paper_only": true,
            "qualification_status": "unqualified", "timeframe": "1m / 5m", "decision_at": "2026-09-20T12:00:00Z",
            "available_at": "2026-09-20T12:00:00Z", "expires_at": "2026-09-20T12:00:15Z",
            "entry_low": "100", "entry_high": "101", "invalidation": "99", "target": "103",
            "reasons": ["trend_aligned", "candidate_confirmed"], "close_reasons": ["invalidation_reached", "target_reached", "trend_alignment_lost", "evidence_expired"]]
        let payload: [String: Any] = ["kind": "published", "protocol_hash": hash, "updated_at": "2026-09-20T12:00:00Z",
            "evaluated_at": "2026-09-20T12:00:00Z", "reasons": [], "suggestion": suggestion]
        return try LivePaperSignalState.decode(JSONSerialization.data(withJSONObject: payload), protocolHash: hash, now: now)
    }
    @Test func levelsRequireCurrentPublicationAndKnownSource() throws {
        let state = try publication()
        let valid = TradeDeskPresentation.rows(state: state, evidence: nil, isRunning: true, source: "binance:spot", now: now)
        #expect(valid[0].detail?.entryLow == "100")
        #expect(valid[1].detail == nil)
        for (running, source, date) in [(false, "binance:spot", now), (true, "unknown", now),
                                       (true, "binance:spot", now.addingTimeInterval(15))] {
            let rows = TradeDeskPresentation.rows(state: state, evidence: nil, isRunning: running, source: source, now: date)
            #expect(rows.allSatisfy { $0.detail == nil && $0.posture == "Stand aside" })
        }
        #expect(TradeDeskPresentation.rows(state: state, evidence: nil, isRunning: true, source: "unknown", now: now)[0].source == "Unavailable")
    }
    @Test func trendAndHistoricalSuccessCannotQualifyCandidate() {
        let context = DayTraderContext(symbol: "BTCUSDT", reportHash: "context", decisionAt: now, expiresAt: now.addingTimeInterval(5),
            regime: "trend", posture: "long_research", trends: [.init(minutes: 1, direction: "up", strength: "1")],
            spreadBps: nil, volatilityBps: nil, quoteImbalance: nil, session: "europe", calendarBlackout: false, reasons: [])
        let outcome = DayTraderHistoricalOutcome(symbol: "BTCUSDT", lifecycleHash: "loss", createdAt: now.addingTimeInterval(-120), completedAt: now.addingTimeInterval(-60), exitReason: "invalidation")
        let evidence = DayTraderEvidence(generatedAt: now, contexts: [context], outcomes: [outcome])
        for kind in ["warming", "abstaining", "failed", "stale", "stopped"] {
            let state = LivePaperSignalState(kind: kind, protocolHash: "p", updatedAt: now, evaluatedAt: nil, reasons: ["candidate_unqualified"], suggestion: nil)
            #expect(TradeDeskPresentation.rows(state: state, evidence: evidence, isRunning: true, source: "binance:spot", now: now).allSatisfy { $0.detail == nil })
        }
        let history = TradeDeskPresentation.history(evidence: evidence)
        #expect(history.count == 1)
        #expect(history[0].outcome == "Invalidation")
        #expect(history[0].costs == "Unavailable")
    }
    @Test func noObservationsRemainUnavailable() {
        let rows = TradeDeskPresentation.rows(state: nil, evidence: nil, isRunning: false, source: nil, now: now)
        #expect(rows.map(\.symbol) == ["BTCUSDT", "ETHUSDT"])
        #expect(rows.allSatisfy { $0.detail == nil && $0.trend == "Unavailable" })
    }
    @Test func freshPublicationCannotOverrideFailedOrStaleProvider() throws {
        let state = try publication()
        for status in ["Feed error", "Stale", "Unavailable"] {
            let rows = TradeDeskPresentation.rows(state: state, evidence: nil, isRunning: true,
                source: "binance:spot", now: now, feedStatus: status)
            #expect(rows.allSatisfy { $0.detail == nil && $0.freshness == status })
        }
    }
    @Test func fixtureRootRequiresMarkerAndNeverFallsBackOnInvalidOverride() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "UIAcceptanceFixtures/\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        #expect(throws: (any Error).self) { try AppStorageLocations.validatedAcceptanceRoot(root.path) }
        try Data("UI TEST ONLY".utf8).write(to: root.appending(path: "UI-TEST-ONLY.md"))
        #expect(try AppStorageLocations.validatedAcceptanceRoot(root.path) == root.resolvingSymlinksInPath())
        #expect(throws: (any Error).self) { try AppStorageLocations.validatedAcceptanceRoot("/tmp") }
    }
}
