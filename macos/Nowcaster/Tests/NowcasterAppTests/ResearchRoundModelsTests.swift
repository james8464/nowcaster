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
        "provider_health": ["provider": "binance", "feed": "spot", "revision": "binance-spot-public-v1",
            "reported_at": "2026-09-20T12:00:00Z", "last_successful_observation_at": NSNull(),
            "maximum_age_seconds": 15, "state": "unavailable", "exclusions": ["no_available_observations"]],
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
    #expect(snapshot.providerHealth.state == "unavailable")
    #expect(snapshot.candidates.count == 1)
    #expect(snapshot.candidates[0].direction == .long)
    #expect(ResearchRoundPresentation(snapshot: snapshot).title.contains("paper-only"))
}

@Test func providerHealthMustBePublishedBoundedAndExpireWhenViewed() throws {
    var payload = validResearchRoundPayload()
    var health: [String: Any] = [
        "provider": "binance", "feed": "spot", "revision": "binance-spot-public-v1",
        "reported_at": "2026-09-20T12:00:00Z", "last_successful_observation_at": "2026-09-20T12:00:00Z",
        "maximum_age_seconds": 15, "state": "healthy", "exclusions": [],
    ]
    payload["provider_health"] = health
    let snapshot = try decodeResearchRound(payload)
    let now = ISO8601DateFormatter().date(from: "2026-09-20T12:00:00Z")!
    #expect(ResearchRoundPresentation(snapshot: snapshot, now: now).providerHealthTitle == "Healthy")
    #expect(ResearchRoundPresentation(snapshot: snapshot, now: now.addingTimeInterval(16)).providerHealthTitle == "Stale")
    for (key, value) in [("state", "qualified"), ("feed", "margin"), ("reported_at", "yesterday"),
                         ("last_successful_observation_at", "2026-09-20T13:00:00Z")] {
        var invalidHealth = health
        invalidHealth[key] = value
        payload["provider_health"] = invalidHealth
        #expect(throws: SnapshotValidationError.self) { try decodeResearchRound(payload) }
    }
    health["exclusions"] = Array(repeating: "error", count: 17)
    payload["provider_health"] = health
    #expect(throws: SnapshotValidationError.self) { try decodeResearchRound(payload) }
    payload.removeValue(forKey: "provider_health")
    #expect(throws: SnapshotValidationError.self) { try decodeResearchRound(payload) }
}

@Test func decodesBundledRoundTwoStandAsideFixture() throws {
    let url = try #require(
        Bundle.module.url(
            forResource: "research-round-2-summary",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let snapshot = try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: Data(contentsOf: url))

    #expect(snapshot.paperOnly)
    #expect(snapshot.qualificationStatus == "unqualified")
    #expect(snapshot.status == .insufficientData)
    #expect(snapshot.candidates.isEmpty)
    #expect(ResearchRoundPresentation(snapshot: snapshot).statusTitle == "Insufficient data — stand aside")
}

@Test func decodesRuntimeOutputWireFormat() throws {
    let url: URL
    if let runtimePath = ProcessInfo.processInfo.environment["NOWCASTER_RESEARCH_ROUND_REPORT_PATH"] {
        url = URL(fileURLWithPath: runtimePath)
    } else {
        url = try #require(
            Bundle.module.url(
                forResource: "research-round-2-summary",
                withExtension: "json",
                subdirectory: "Fixtures"
            )
        )
    }

    let snapshot = try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: Data(contentsOf: url))
    #expect(snapshot.paperOnly)
    #expect(snapshot.qualificationStatus == "unqualified")
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
