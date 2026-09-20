import Foundation
import Testing

@testable import NowcasterApp

private func validResearchRoundPayload() -> [String: Any] {
    [
        "round_id": "round-two-demo",
        "protocol_hash": String(repeating: "a", count: 64),
        "status": "experimental_paper_only",
        "paper_only": true,
        "qualification_status": "unqualified",
        "reasons": ["experimental_paper_only"],
        "candidates": [[
            "symbol": "BTCUSDT",
            "strategy_id": "ema",
            "direction": "long",
            "status": "experimental_paper_only",
            "paper_only": true,
            "qualification_status": "unqualified",
            "reasons": ["all_gates_passed"],
            "sealed_metrics": [
                "net_return": "0.02",
                "stressed_net_return": "0.01",
                "lower_edge": "0.001",
                "trade_count": 100,
                "maximum_drawdown": "0.05",
                "coverage": "0.999",
            ],
        ]],
    ]
}

private func decodeResearchRound(_ payload: [String: Any]) throws -> ResearchRoundSnapshot {
    try JSONDecoder.nowcaster.decode(
        ResearchRoundSnapshot.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

@Test func decodesBoundedUnqualifiedPaperOnlyRound() throws {
    let snapshot = try decodeResearchRound(validResearchRoundPayload())

    #expect(snapshot.paperOnly)
    #expect(snapshot.qualificationStatus == "unqualified")
    #expect(snapshot.providerHealth == .notPublished)
    #expect(snapshot.candidates.count == 1)
    #expect(snapshot.candidates[0].direction == .long)
    #expect(ResearchRoundPresentation(snapshot: snapshot).title.contains("paper-only"))
}

@Test func rejectsQualifiedOrderOrShortSpotCandidate() throws {
    var payload = validResearchRoundPayload()
    payload["qualification_status"] = "qualified"
    #expect(throws: SnapshotValidationError.self) {
        try decodeResearchRound(payload)
    }

    payload = validResearchRoundPayload()
    payload["candidates"] = [[
        "symbol": "BTCUSDT", "strategy_id": "ema", "direction": "short",
        "status": "experimental_paper_only", "paper_only": true,
        "qualification_status": "unqualified", "reasons": [],
        "sealed_metrics": ["net_return": "0", "stressed_net_return": "0", "lower_edge": NSNull(),
                           "trade_count": 0, "maximum_drawdown": "0", "coverage": "0"],
    ]]
    #expect(throws: SnapshotValidationError.self) {
        try decodeResearchRound(payload)
    }

    payload = validResearchRoundPayload()
    payload["order_id"] = "must-not-cross"
    #expect(throws: SnapshotValidationError.self) {
        try decodeResearchRound(payload)
    }
}

@Test func rejectsCandidateLifecycleAndUnrecognisedPayloadFields() throws {
    var payload = validResearchRoundPayload()
    var candidate = try #require((payload["candidates"] as? [[String: Any]])?.first)
    candidate["notification_lifecycle"] = "open"
    payload["candidates"] = [candidate]

    #expect(throws: SnapshotValidationError.self) {
        try decodeResearchRound(payload)
    }
}
