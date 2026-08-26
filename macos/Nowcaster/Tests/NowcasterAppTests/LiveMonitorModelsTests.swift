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
