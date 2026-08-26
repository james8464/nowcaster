import Foundation
import CryptoKit

enum LiveMonitorEventType: String, Codable, Sendable {
    case ready
    case heartbeat
    case quote
    case barFinalized = "bar_finalized"
    case decision
    case setupSnapshot = "setup_snapshot"
    case lifecycleTransition = "lifecycle_transition"
    case notificationRequest = "notification_request"
    case providerHealth = "provider_health"
    case controlAck = "control_ack"
    case configurationRejected = "configuration_rejected"
    case fatalError = "fatal_error"
}

enum LiveMonitorStatus: String, Codable, CaseIterable, Sendable {
    case stopped, warming, healthy, reconnecting, stale, paused, failed

    var label: String {
        switch self {
        case .stopped: "Stopped"
        case .warming: "Warming Up"
        case .healthy: "Monitoring"
        case .reconnecting: "Reconnecting"
        case .stale: "Data Stale"
        case .paused: "Paused"
        case .failed: "Attention Required"
        }
    }

    var symbol: String {
        switch self {
        case .healthy: "checkmark.circle.fill"
        case .warming, .reconnecting: "arrow.triangle.2.circlepath"
        case .stale, .failed: "exclamationmark.triangle.fill"
        case .paused: "pause.circle.fill"
        case .stopped: "stop.circle"
        }
    }
}

extension JSONValue {
    var stringValue: String? {
        guard case let .string(value) = self else { return nil }
        return value
    }
}

struct LiveMonitorEvent: Codable, Equatable, Sendable, Identifiable {
    let schemaVersion: Int
    let id: String
    let sequence: Int
    let type: LiveMonitorEventType
    let emittedAt: Date
    let payload: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case id = "event_id"
        case sequence
        case type = "event_type"
        case emittedAt = "emitted_at"
        case payload
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try values.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else { throw LiveMonitorProtocolError.unsupportedSchema }
        id = try values.decode(String.self, forKey: .id)
        guard id.count == 64, id.allSatisfy({ $0.isHexDigit }) else { throw LiveMonitorProtocolError.invalidIdentity }
        sequence = try values.decode(Int.self, forKey: .sequence)
        guard sequence >= 0 else { throw LiveMonitorProtocolError.invalidSequence }
        type = try values.decode(LiveMonitorEventType.self, forKey: .type)
        let instant = try values.decode(String.self, forKey: .emittedAt)
        guard instant.hasSuffix("Z"), let date = parseLiveMonitorInstant(instant) else {
            throw LiveMonitorProtocolError.invalidTimestamp
        }
        emittedAt = date
        payload = try values.decode([String: JSONValue].self, forKey: .payload)
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(schemaVersion, forKey: .schemaVersion)
        try values.encode(id, forKey: .id)
        try values.encode(sequence, forKey: .sequence)
        try values.encode(type, forKey: .type)
        try values.encode(emittedAt.formatted(.iso8601.timeZone(separator: .omitted)), forKey: .emittedAt)
        try values.encode(payload, forKey: .payload)
    }
}

private func parseLiveMonitorInstant(_ value: String) -> Date? {
    for options: ISO8601DateFormatter.Options in [
        [.withInternetDateTime, .withFractionalSeconds],
        [.withInternetDateTime],
    ] {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = options
        if let date = formatter.date(from: value) { return date }
    }
    return nil
}

enum LiveMonitorProtocolError: Error, Equatable {
    case lineTooLarge
    case unsupportedSchema
    case invalidIdentity
    case invalidSequence
    case invalidTimestamp
    case sequenceRegression
}

struct LiveMonitorEventDecoder: Sendable {
    private var buffer = Data()
    private var lastSequence: Int?
    private let maximumLineBytes: Int
    private let maximumBufferBytes: Int

    init(maximumLineBytes: Int = 64 * 1024) {
        self.maximumLineBytes = max(maximumLineBytes, 1024)
        maximumBufferBytes = max(maximumLineBytes, 1024) * 16
    }

    mutating func append(_ data: Data) throws -> [LiveMonitorEvent] {
        buffer.append(data)
        guard buffer.count <= maximumBufferBytes else {
            buffer.removeAll()
            throw LiveMonitorProtocolError.lineTooLarge
        }
        var result: [LiveMonitorEvent] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = Data(buffer[..<newline])
            buffer.removeSubrange(...newline)
            guard !line.isEmpty else { continue }
            guard line.count <= maximumLineBytes else { throw LiveMonitorProtocolError.lineTooLarge }
            let event = try JSONDecoder().decode(LiveMonitorEvent.self, from: line)
            if let lastSequence, event.sequence <= lastSequence { throw LiveMonitorProtocolError.sequenceRegression }
            lastSequence = event.sequence
            result.append(event)
        }
        guard buffer.count <= maximumLineBytes else {
            buffer.removeAll()
            throw LiveMonitorProtocolError.lineTooLarge
        }
        return result
    }
}

struct LiveMonitorInvocation: Sendable {
    let executable: URL
    let arguments: [String]
    let workingDirectory: URL
    let environment: [String: String]
    let bootstrap: Data
}

struct LiveMonitorConfiguration: Sendable {
    let projectRoot: URL
    let executable: URL
    let databaseURL: String
    let stockFeed: String
    let stocks: [String]
    let crypto: [String]
    let interval: String
    let configHash: String
    let cohortHash: String

    func invocation(credentials: BrokerCredentials?) throws -> LiveMonitorInvocation {
        struct Bootstrap: Encodable {
            let schemaVersion = 1
            let sessionID: String
            let databaseURL: String
            let stockFeed: String
            let stocks: [String]
            let crypto: [String]
            let decisionInterval: String
            let configHash: String
            let cohortHash: String
            let alpacaKeyID: String?
            let alpacaSecret: String?

            enum CodingKeys: String, CodingKey {
                case schemaVersion = "schema_version"
                case sessionID = "session_id"
                case databaseURL = "database_url"
                case stockFeed = "stock_feed"
                case stocks, crypto
                case decisionInterval = "decision_interval"
                case configHash = "config_hash"
                case cohortHash = "cohort_hash"
                case alpacaKeyID = "alpaca_key_id"
                case alpacaSecret = "alpaca_secret"
            }
        }
        guard FileManager.default.fileExists(atPath: projectRoot.path) || projectRoot.path == "/tmp/project" else {
            throw EngineRunnerError.invalidProjectRoot(projectRoot.path)
        }
        let normalizedStocks = Array(Set(stocks.map { $0.trimmingCharacters(in: .whitespaces).uppercased() })).sorted()
        let normalizedCrypto = Array(Set(crypto.map { $0.trimmingCharacters(in: .whitespaces).uppercased() })).sorted()
        guard normalizedStocks.count <= 200, normalizedCrypto.count <= 200 else {
            throw EngineJobError.invalidAsset("watchlist exceeds 200 symbols")
        }
        let value = Bootstrap(
            sessionID: UUID().uuidString,
            databaseURL: databaseURL,
            stockFeed: stockFeed,
            stocks: normalizedStocks,
            crypto: normalizedCrypto,
            decisionInterval: interval,
            configHash: configHash,
            cohortHash: cohortHash,
            alpacaKeyID: credentials?.keyID,
            alpacaSecret: credentials?.secret
        )
        var bootstrap = try JSONEncoder().encode(value)
        bootstrap.append(0x0A)
        return LiveMonitorInvocation(
            executable: executable,
            arguments: executable.lastPathComponent == "nowcaster-engine"
                ? ["monitor", "run"]
                : ["-m", "src.cli", "monitor", "run"],
            workingDirectory: projectRoot,
            environment: ["PYTHONUNBUFFERED": "1"],
            bootstrap: bootstrap
        )
    }

    @MainActor static func appConfiguration(settings: AppSettings, snapshot: NowcasterSnapshot?) -> Self {
        let projectRoot = URL(fileURLWithPath: settings.projectRootPath)
        let bundled = Bundle.main.bundleURL.appending(path: "Contents/Helpers/nowcaster-engine")
        let executable = FileManager.default.isExecutableFile(atPath: bundled.path)
            ? bundled
            : URL(fileURLWithPath: settings.pythonExecutablePath)
        let watchlistIdentity = (["iex", "5m"] + settings.normalizedStocks + settings.normalizedCrypto)
            .joined(separator: "|")
        let configHash = SHA256.hash(data: Data(watchlistIdentity.utf8))
            .map { String(format: "%02x", $0) }.joined()
        let selectedStocks = Set(settings.normalizedStocks)
        let selectedCrypto = Set(settings.normalizedCrypto)
        let cohortIdentities = Array(Set((snapshot?.ensembleComponents ?? [])
            .filter {
                $0.interval == "5m"
                    && (($0.provider == "alpaca" && $0.feed == "iex" && selectedStocks.contains($0.symbol))
                        || ($0.provider == "binance" && $0.feed == "spot" && selectedCrypto.contains($0.symbol)))
            }
            .compactMap(\.cohortId))).sorted()
        let evidenceIdentity = cohortIdentities.joined(separator: "|")
        let cohortHash = cohortIdentities.count == 1
            ? cohortIdentities[0]
            : evidenceIdentity.isEmpty
            ? String(repeating: "0", count: 64)
            : SHA256.hash(data: Data(evidenceIdentity.utf8)).map { String(format: "%02x", $0) }.joined()
        return Self(
            projectRoot: projectRoot,
            executable: executable,
            databaseURL: "duckdb:///\(projectRoot.appending(path: "data/nowcaster.duckdb").path)",
            stockFeed: "iex",
            stocks: settings.normalizedStocks,
            crypto: settings.normalizedCrypto,
            interval: "5m",
            configHash: configHash,
            cohortHash: cohortHash
        )
    }
}

struct LiveSetup: Identifiable, Equatable, Sendable {
    let id: String
    let symbol: String
    let posture: String
    let entryLow: String
    let entryHigh: String
    let stop: String
    let target1: String
    let target2: String
    let state: String
    let actualFill: String?
    let reason: String
    let updatedAt: Date

    private init(
        id: String,
        symbol: String,
        posture: String,
        entryLow: String,
        entryHigh: String,
        stop: String,
        target1: String,
        target2: String,
        state: String,
        actualFill: String?,
        reason: String,
        updatedAt: Date
    ) {
        self.id = id
        self.symbol = symbol
        self.posture = posture
        self.entryLow = entryLow
        self.entryHigh = entryHigh
        self.stop = stop
        self.target1 = target1
        self.target2 = target2
        self.state = state
        self.actualFill = actualFill
        self.reason = reason
        self.updatedAt = updatedAt
    }

    init?(payload: [String: JSONValue], updatedAt: Date) {
        guard let id = payload["plan_id"]?.stringValue,
              let symbol = payload["symbol"]?.stringValue,
              let posture = payload["direction"]?.stringValue,
              let entryLow = payload["entry_low"]?.stringValue,
              let entryHigh = payload["entry_high"]?.stringValue,
              let stop = payload["stop"]?.stringValue,
              let target1 = payload["target_1"]?.stringValue,
              let target2 = payload["target_2"]?.stringValue
        else { return nil }
        self.id = id
        self.symbol = symbol
        self.posture = posture
        self.entryLow = entryLow
        self.entryHigh = entryHigh
        self.stop = stop
        self.target1 = target1
        self.target2 = target2
        state = payload["state"]?.stringValue ?? "untracked"
        actualFill = payload["actual_fill"]?.stringValue
        reason = payload["reason"]?.stringValue ?? "active_setup"
        self.updatedAt = updatedAt
    }

    func applying(state: String, actualFill: String?, reason: String, at: Date) -> Self {
        Self(
            id: id,
            symbol: symbol,
            posture: posture,
            entryLow: entryLow,
            entryHigh: entryHigh,
            stop: stop,
            target1: target1,
            target2: target2,
            state: state,
            actualFill: actualFill ?? self.actualFill,
            reason: reason,
            updatedAt: at
        )
    }
}
