import Foundation
import Testing
@testable import NowcasterApp

private func advisorPayload() -> [String: Any] {
    ["symbol": "BTCUSDT", "strategy_id": "trend", "round_id": "round",
     "protocol_hash": String(repeating: "a", count: 64), "source_hash": String(repeating: "b", count: 64),
     "candidate_hash": String(repeating: "c", count: 64), "evidence_hash": String(repeating: "d", count: 64),
     "policy_hash": String(repeating: "e", count: 64), "source": "binance:spot",
     "posture": "long_research", "paper_only": true, "qualification_status": "unqualified", "timeframe": "1m / 5m",
     "decision_at": "2026-09-20T12:00:00Z", "available_at": "2026-09-20T12:00:00Z",
     "expires_at": "2026-09-20T12:00:15Z", "entry_low": "100", "entry_high": "101", "invalidation": "99",
     "target": "103", "reasons": ["trend_aligned", "candidate_confirmed"],
     "close_reasons": ["invalidation_reached", "target_reached", "trend_alignment_lost", "evidence_expired"]]
}

private func decodeAdvisor(_ payload: [String: Any]) throws -> TrendAdvisorSuggestion {
    try JSONDecoder.nowcaster.decode(TrendAdvisorSuggestion.self, from: JSONSerialization.data(withJSONObject: payload))
}

@Suite struct TrendAdvisorModelsTests {
    @Test func showsResearchLevelsOnlyUntilEvidenceExpires() throws {
        let item = try decodeAdvisor(advisorPayload())
        let fresh = TrendAdvisorPresentation(suggestion: item, now: item.decisionAt)
        #expect(fresh.postureTitle == "Long research posture")
        #expect(fresh.showsLevels)
        let expired = TrendAdvisorPresentation(suggestion: item, now: item.expiresAt)
        #expect(expired.postureTitle == "Stand aside")
        #expect(!expired.showsLevels)
        #expect(!TrendAdvisorPresentation(suggestion: item, now: item.decisionAt.addingTimeInterval(-1)).showsLevels)
    }

    @Test func rejectsShortQualificationActionsFutureEvidenceAndInvalidLevels() throws {
        for (key, value) in [
            ("posture", "short_research"), ("qualification_status", "qualified"), ("order_id", "x"),
            ("notification", "x"), ("lifecycle", "x"), ("available_at", "2026-09-20T12:00:01Z"),
            ("expires_at", "2026-09-20T13:00:00Z"), ("target", "99"), ("entry_low", "NaN"),
            ("source", "broker:margin"), ("protocol_hash", "x"),
        ] {
            var payload = advisorPayload()
            payload[key] = value
            #expect(throws: SnapshotValidationError.self) { try decodeAdvisor(payload) }
        }
    }

    @Test func standAsideCannotCarryLevels() throws {
        var payload = advisorPayload()
        payload["posture"] = "stand_aside"
        payload["reasons"] = ["spot_short_unsupported"]
        #expect(throws: SnapshotValidationError.self) { try decodeAdvisor(payload) }
        for key in ["entry_low", "entry_high", "invalidation", "target"] { payload[key] = NSNull() }
        let item = try decodeAdvisor(payload)
        #expect(!TrendAdvisorPresentation(suggestion: item, now: item.decisionAt).showsLevels)
    }

    @Test func delayedDecisionCannotRenewEvidenceAndRenderingExpiresAtEvidenceDeadline() throws {
        var payload = advisorPayload()
        payload["decision_at"] = "2026-09-20T12:00:14Z"
        payload["expires_at"] = "2026-09-20T12:00:29Z"
        #expect(throws: SnapshotValidationError.self) { try decodeAdvisor(payload) }
        payload["expires_at"] = "2026-09-20T12:00:15Z"
        let item = try decodeAdvisor(payload)
        #expect(TrendAdvisorPresentation(suggestion: item, now: item.decisionAt).showsLevels)
        #expect(!TrendAdvisorPresentation(suggestion: item, now: item.availableAt!.addingTimeInterval(15)).showsLevels)
    }

    @Test func reportBindsAdvisorIdentityAndCandidateStatus() throws {
        var payload: [String: Any] = ["round_id": "round", "protocol_hash": String(repeating: "a", count: 64),
            "status": "experimental_paper_only", "paper_only": true, "qualification_status": "unqualified",
            "reasons": [], "candidates": [["symbol": "BTCUSDT", "strategy_id": "trend", "direction": "long",
                "candidate_hash": String(repeating: "c", count: 64),
                "status": "experimental_paper_only", "paper_only": true, "qualification_status": "unqualified",
                "reasons": [], "sealed_metrics": ["net_return": "0.1", "stressed_net_return": "0.05", "lower_edge": "0.01",
                "trade_count": 100, "maximum_drawdown": "0.05", "coverage": "1"]]], "trend_advisor": [advisorPayload()]]
        func decode() throws -> ResearchRoundSnapshot {
            try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: JSONSerialization.data(withJSONObject: payload))
        }
        #expect(try decode().trendAdvisor.count == 1)
        var mismatchedAdvisor = advisorPayload()
        mismatchedAdvisor["candidate_hash"] = String(repeating: "f", count: 64)
        payload["trend_advisor"] = [mismatchedAdvisor]
        #expect(throws: SnapshotValidationError.self) { try decode() }
        payload["trend_advisor"] = [advisorPayload()]
        payload["protocol_hash"] = String(repeating: "f", count: 64)
        #expect(throws: SnapshotValidationError.self) { try decode() }
        payload["protocol_hash"] = String(repeating: "a", count: 64)
        payload["candidates"] = []
        #expect(throws: SnapshotValidationError.self) { try decode() }
    }
}
