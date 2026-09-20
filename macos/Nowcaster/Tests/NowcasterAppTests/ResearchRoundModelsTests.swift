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

@Test func candidatePresentationFailsClosedForRejectedAndInsufficientEvidence() throws {
    var payload = validResearchRoundPayload()
    payload["candidates"] = [
        [
            "symbol": "BTCUSDT", "strategy_id": "ema", "direction": "long",
            "status": "experimental_paper_only", "paper_only": true,
            "qualification_status": "unqualified", "reasons": ["gates_passed"],
            "sealed_metrics": ["net_return": "0", "stressed_net_return": "0", "lower_edge": NSNull(),
                               "trade_count": 100, "maximum_drawdown": "0", "coverage": "1"],
        ],
        [
            "symbol": "ETHUSDT", "strategy_id": "ema", "direction": "long",
            "status": "rejected", "paper_only": true,
            "qualification_status": "unqualified", "reasons": ["validation_lower_edge"],
            "sealed_metrics": ["net_return": "-0.01", "stressed_net_return": "-0.02", "lower_edge": "-0.03",
                               "trade_count": 100, "maximum_drawdown": "0.05", "coverage": "1"],
        ],
    ]
    let snapshot = try decodeResearchRound(payload)

    #expect(ResearchRoundCandidatePresentation(candidate: snapshot.candidates[0]).directionTitle == "Long research only")
    let rejected = ResearchRoundCandidatePresentation(candidate: snapshot.candidates[1])
    #expect(rejected.directionTitle == "Stand aside")
    #expect(rejected.reasonTitle.contains("validation lower edge"))
}

@Test @MainActor func modelRetainsOnlyAValidatedResearchRoundReport() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterResearchRound-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let report = directory.appending(path: "research-round-2-summary.json")
    try JSONSerialization.data(withJSONObject: validResearchRoundPayload()).write(to: report)

    let model = AppModel()
    await model.loadResearchRound(url: report)
    #expect(model.researchRoundSnapshot?.roundID == "round-two-demo")
    #expect(model.researchRoundLoadMessage == nil)

    model.rejectResearchRoundImport(CocoaError(.fileReadNoPermission))
    #expect(model.researchRoundSnapshot == nil)
    #expect(model.researchRoundLoadMessage?.contains("rejected") == true)

    try Data("{\"qualification_status\":\"qualified\"}".utf8).write(to: report)
    await model.loadResearchRound(url: report)
    #expect(model.researchRoundSnapshot == nil)
    #expect(model.researchRoundLoadMessage?.contains("rejected") == true)
}
