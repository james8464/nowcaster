import Foundation
import Testing

@testable import NowcasterApp

@Test func liveMonitorDecoderIsIncrementalStrictAndBoundsLines() throws {
    let line = #"{"schema_version":1,"event_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sequence":0,"event_type":"provider_health","emitted_at":"2026-08-26T14:01:02Z","payload":{"provider":"alpaca","feed":"iex","status":"healthy","reason":"authenticated","occurred_at":"2026-08-26T14:01:02Z"}}"#
    var decoder = LiveMonitorEventDecoder(maximumLineBytes: 4096)

    let first = try decoder.append(Data(line.prefix(80).utf8))
    let second = try decoder.append(Data((line.dropFirst(80) + "\n").utf8))

    #expect(first.isEmpty)
    #expect(second.count == 1)
    #expect(second[0].type == .providerHealth)
    #expect(second[0].sequence == 0)

    var bounded = LiveMonitorEventDecoder(maximumLineBytes: 1024)
    #expect(throws: LiveMonitorProtocolError.lineTooLarge) {
        try bounded.append(Data(repeating: 0x61, count: 1025))
    }
    var trailing = LiveMonitorEventDecoder(maximumLineBytes: 1024)
    #expect(throws: LiveMonitorProtocolError.lineTooLarge) {
        try trailing.append(Data((line + "\n" + String(repeating: "x", count: 1025)).utf8))
    }
}

@Test func liveMonitorDecoderRejectsUnknownSchemaTypeAndNonZuluTime() {
    let invalid = [
        #"{"schema_version":2,"event_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sequence":0,"event_type":"heartbeat","emitted_at":"2026-08-26T14:01:02Z","payload":{}}"#,
        #"{"schema_version":1,"event_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sequence":0,"event_type":"trade","emitted_at":"2026-08-26T14:01:02Z","payload":{}}"#,
        #"{"schema_version":1,"event_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sequence":0,"event_type":"heartbeat","emitted_at":"2026-08-26T14:01:02+00:00","payload":{}}"#,
    ]
    for line in invalid {
        var decoder = LiveMonitorEventDecoder()
        #expect(throws: (any Error).self) { try decoder.append(Data((line + "\n").utf8)) }
    }
}

@Test func invocationKeepsCredentialsInBootstrapRatherThanArgumentsOrEnvironment() throws {
    let configuration = LiveMonitorConfiguration(
        projectRoot: URL(fileURLWithPath: "/tmp/project"),
        executable: URL(fileURLWithPath: "/tmp/python"),
        databaseURL: "duckdb:////tmp/monitor.duckdb",
        stockFeed: "iex",
        stocks: ["AAPL"],
        crypto: ["BTCUSDT"],
        interval: "5m",
        configHash: String(repeating: "c", count: 64),
        cohortHash: String(repeating: "d", count: 64)
    )
    let invocation = try configuration.invocation(
        credentials: BrokerCredentials(keyID: "private-key", secret: "private-secret")
    )
    let bootstrap = String(decoding: invocation.bootstrap, as: UTF8.self)

    #expect(invocation.arguments == ["-m", "src.cli", "monitor", "run"])
    #expect(Set(invocation.environment.keys) == ["PYTHONUNBUFFERED"])
    #expect(!invocation.arguments.joined().contains("private-key"))
    #expect(bootstrap.contains("private-key"))
    #expect(bootstrap.contains("private-secret"))
}

@Test func bundledEngineUsesItsNativeCLIContract() throws {
    let configuration = LiveMonitorConfiguration(
        projectRoot: URL(fileURLWithPath: "/tmp/project"),
        executable: URL(fileURLWithPath: "/tmp/nowcaster-engine"),
        databaseURL: "duckdb:////tmp/monitor.duckdb",
        stockFeed: "iex",
        stocks: ["AAPL"],
        crypto: [],
        interval: "5m",
        configHash: String(repeating: "c", count: 64),
        cohortHash: String(repeating: "d", count: 64)
    )

    #expect(try configuration.invocation(credentials: nil).arguments == ["monitor", "run"])
}

@Test func typedActiveSetupSurvivesDiagnosticEventEvictionAndAppliesTrackingState() throws {
    let payload: [String: JSONValue] = [
        "plan_id": .string(String(repeating: "a", count: 64)),
        "symbol": .string("AAPL"),
        "direction": .string("long"),
        "entry_low": .string("100"),
        "entry_high": .string("100.10"),
        "stop": .string("99"),
        "target_1": .string("102"),
        "target_2": .string("103"),
        "state": .string("untracked"),
    ]
    let setup = try #require(LiveSetup(payload: payload, updatedAt: .now))
    let tracked = setup.applying(
        state: "tracked",
        actualFill: "100.04",
        reason: "operator_fill_tracked",
        at: .now
    )

    #expect(tracked.id == setup.id)
    #expect(tracked.state == "tracked")
    #expect(tracked.actualFill == "100.04")
}

@Test func experimentalOpportunityRequiresTheCompletePaperOnlyWirePayload() throws {
    let payload: [String: JSONValue] = [
        "plan_id": .string(String(repeating: "e", count: 64)),
        "provider": .string("alpaca"),
        "feed": .string("iex"),
        "symbol": .string("AAPL"),
        "decision_interval": .string("5m"),
        "direction": .string("long"),
        "decision_time": .string("2026-09-18T10:00:00Z"),
        "expires_at": .string("2026-09-18T10:25:00Z"),
        "entry_low": .string("100"),
        "entry_high": .string("100.10"),
        "stop": .string("99"),
        "target_1": .string("102"),
        "target_2": .string("103"),
        "risk_per_unit": .string("1"),
        "reward_to_risk_1": .string("2"),
        "reward_to_risk_2": .string("3"),
        "venue_note": .null,
        "cohort_id": .string(String(repeating: "a", count: 64)),
        "dataset_hash": .string(String(repeating: "b", count: 64)),
        "evidence_hash": .string(String(repeating: "c", count: 64)),
        "policy_hash": .string(String(repeating: "d", count: 64)),
        "config_hash": .string(String(repeating: "f", count: 64)),
        "strategy_versions": .array([.array([.string("trend"), .string("v1")])]),
        "experimental_paper_only": .bool(true),
        "qualification_status": .string("unqualified"),
        "qualification_reasons": .array([.string("promotion_required")]),
    ]

    let opportunity = try #require(ExperimentalOpportunity(payload: payload, updatedAt: .now))

    #expect(opportunity.isPaperOnly)
    #expect(opportunity.symbol == "AAPL")
    #expect(opportunity.expiry == "2026-09-18T10:25:00Z")
    #expect(opportunity.qualificationReasons == ["promotion_required"])

    var unsafePayload = payload
    unsafePayload["experimental_paper_only"] = .bool(false)
    #expect(ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil)
    unsafePayload = payload
    unsafePayload.removeValue(forKey: "risk_per_unit")
    #expect(ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil)
    unsafePayload = payload
    unsafePayload["direction"] = .string("neutral")
    #expect(ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil)
    unsafePayload = payload
    unsafePayload["qualification_reasons"] = .array([])
    #expect(ExperimentalOpportunity(payload: unsafePayload, updatedAt: .now) == nil)
}

@Test func mixedProviderHealthUsesWorstSeverityInsteadOfLastWriter() {
    #expect(LiveMonitorHealthAggregation.aggregate([.stale, .healthy]) == .stale)
    #expect(LiveMonitorHealthAggregation.aggregate([.warming, .healthy]) == .warming)
    #expect(LiveMonitorHealthAggregation.aggregate([.reconnecting, .stale]) == .stale)
    #expect(LiveMonitorHealthAggregation.aggregate([.healthy, .healthy]) == .healthy)
}
