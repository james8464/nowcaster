import CryptoKit
import Foundation

struct DayTraderTrend: Equatable, Sendable {
    let minutes: Int
    let direction: String
    let strength: String?
}

struct DayTraderContext: Equatable, Sendable, Identifiable {
    let symbol: String
    let reportHash: String
    let decisionAt: Date
    let expiresAt: Date
    let regime: String
    let posture: String
    let trends: [DayTraderTrend]
    let spreadBps: String?
    let volatilityBps: String?
    let quoteImbalance: String?
    let session: String
    let calendarBlackout: Bool?
    let reasons: [String]
    var id: String { reportHash }
}

/// Completed hypotheses only. This type cannot supply a current research posture.
struct DayTraderHistoricalOutcome: Equatable, Sendable, Identifiable {
    let symbol: String
    let lifecycleHash: String
    let createdAt: Date
    let completedAt: Date
    let exitReason: String
    var id: String { lifecycleHash }
}

struct DayTraderEvidence: Equatable, Sendable {
    let generatedAt: Date
    let contexts: [DayTraderContext]
    let outcomes: [DayTraderHistoricalOutcome]

    func currentContexts(now: Date, isRunning: Bool) -> [DayTraderContext] {
        guard isRunning, generatedAt <= now, now.timeIntervalSince(generatedAt) < 15 else { return [] }
        return contexts.filter { $0.decisionAt <= now && now < $0.expiresAt }
    }

    static func decode(_ data: Data, protocolHash: String, now: Date) throws -> Self {
        guard data.count <= 65_536, case let .object(root) = try JSONDecoder.nowcaster.decode(JSONValue.self, from: data)
        else { throw dayInvalid() }
        try dayKeys(root, ["schemaVersion", "paperOnly", "protocolHash", "contextProtocolHash", "generatedAt", "contexts", "outcomes", "contentHash"])
        guard root["schemaVersion"] == .number(1), root["paperOnly"] == .bool(true),
              try dayHash(root, "protocolHash") == protocolHash else { throw dayInvalid() }
        let contextHash = try dayHash(root, "contextProtocolHash")
        let generated = try dayDate(root, "generatedAt")
        guard generated <= now, now.timeIntervalSince(generated) < 15 else { throw dayInvalid() }
        // The helper verifies original reports and replay chains; this digest binds
        // every projected display field across the native process boundary.
        guard var raw = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw dayInvalid() }
        raw.removeValue(forKey: "content_hash")
        let canonical = try JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys, .withoutEscapingSlashes])
        let digest = SHA256.hash(data: canonical).map { String(format: "%02x", $0) }.joined()
        guard try dayHash(root, "contentHash") == digest else { throw dayInvalid() }
        let contexts = try dayArray(root, "contexts", limit: 2).map { value -> DayTraderContext in
            guard case let .object(row) = value else { throw dayInvalid() }
            try dayKeys(row, ["symbol", "protocolHash", "contextProtocolHash", "reportHash", "featureHash", "decisionAt", "availableAt", "expiresAt", "regime", "posture", "trends", "spreadBps", "volatilityBps", "quoteImbalance", "session", "calendarBlackout", "reasons"])
            try dayIdentity(row, protocolHash, contextHash)
            _ = try dayHash(row, "featureHash")
            let decision = try dayDate(row, "decisionAt"), available = try dayDate(row, "availableAt")
            let expiry = try dayDate(row, "expiresAt")
            guard available <= decision, decision <= generated, now < expiry,
                  expiry.timeIntervalSince(decision) <= 15 else { throw dayInvalid() }
            let regime = try dayEnum(row, "regime", ["trend", "range", "volatile", "illiquid", "unknown"])
            let posture = try dayEnum(row, "posture", ["long_research", "stand_aside"])
            let trends = try dayArray(row, "trends", limit: 3).map { value -> DayTraderTrend in
                guard case let .object(trend) = value else { throw dayInvalid() }
                try dayKeys(trend, ["minutes", "direction", "strength"])
                guard case let .number(minutes) = trend["minutes"], [1, 5, 15].contains(minutes) else { throw dayInvalid() }
                return DayTraderTrend(minutes: Int(minutes), direction: try dayEnum(trend, "direction", ["up", "down", "flat", "unavailable"]),
                                      strength: try dayDecimal(trend, "strength", minimum: 0, maximum: 1))
            }
            guard trends.map(\.minutes) == [1, 5, 15] else { throw dayInvalid() }
            let spread = try dayDecimal(row, "spreadBps", minimum: 0, maximum: 20_000)
            let volatility = try dayDecimal(row, "volatilityBps", minimum: 0, maximum: 1e30)
            let imbalance = try dayDecimal(row, "quoteImbalance", minimum: -1, maximum: 1)
            let blackout: Bool?
            switch row["calendarBlackout"] {
            case let .bool(value): blackout = value
            case .null: blackout = nil
            default: throw dayInvalid()
            }
            let reasons = try dayArray(row, "reasons", limit: 64).map { value -> String in
                guard case let .string(reason) = value, !reason.isEmpty, reason.count <= 128,
                      reason.allSatisfy({ "abcdefghijklmnopqrstuvwxyz0123456789_".contains($0) }),
                      Set(reason.split(separator: "_").map(String.init)).isDisjoint(with: ["buy", "sell", "order", "execute", "profit", "guaranteed"])
                else { throw dayInvalid() }
                return reason
            }
            if posture == "long_research" {
                guard regime == "trend", reasons.isEmpty, blackout == false,
                      spread != nil, volatility != nil, imbalance != nil,
                      trends.allSatisfy({ $0.direction == "up" && (Double($0.strength ?? "") ?? -1) >= 0.5 })
                else { throw dayInvalid() }
            }
            return DayTraderContext(symbol: try dayEnum(row, "symbol", ["BTCUSDT", "ETHUSDT"]), reportHash: try dayHash(row, "reportHash"),
                decisionAt: decision, expiresAt: expiry, regime: regime, posture: posture, trends: trends, spreadBps: spread,
                volatilityBps: volatility, quoteImbalance: imbalance,
                session: try dayEnum(row, "session", ["asia", "europe", "europe_americas_overlap", "americas", "overnight"]),
                calendarBlackout: blackout, reasons: reasons)
        }
        guard Set(contexts.map(\.symbol)).count == contexts.count else { throw dayInvalid() }
        let outcomes = try dayArray(root, "outcomes", limit: 30).map { value -> DayTraderHistoricalOutcome in
            guard case let .object(row) = value else { throw dayInvalid() }
            try dayKeys(row, ["symbol", "protocolHash", "contextProtocolHash", "lifecycleHash", "recordHash", "originReportHash", "createdAt", "completedAt", "exitReason", "revision"])
            try dayIdentity(row, protocolHash, contextHash)
            _ = try dayHash(row, "recordHash"); _ = try dayHash(row, "originReportHash")
            let created = try dayDate(row, "createdAt"), completed = try dayDate(row, "completedAt")
            guard created < completed, completed <= generated,
                  case let .number(revision) = row["revision"], revision >= 1, revision <= 10_000,
                  revision.rounded() == revision else { throw dayInvalid() }
            return DayTraderHistoricalOutcome(symbol: try dayEnum(row, "symbol", ["BTCUSDT", "ETHUSDT"]),
                lifecycleHash: try dayHash(row, "lifecycleHash"), createdAt: created, completedAt: completed,
                exitReason: try dayEnum(row, "exitReason", ["expired", "invalidation", "target", "regime_change", "time_limit"]))
        }
        guard Set(outcomes.map(\.id)).count == outcomes.count,
              outcomes.map(\.completedAt) == outcomes.map(\.completedAt).sorted() else { throw dayInvalid() }
        return Self(generatedAt: generated, contexts: contexts, outcomes: outcomes)
    }
}

private func dayInvalid() -> SnapshotValidationError { .invalidResearchEvidence("Decision context is unavailable or failed validation.") }
private func dayKeys(_ row: [String: JSONValue], _ keys: Set<String>) throws {
    guard Set(row.keys) == keys else { throw dayInvalid() }
}
private func dayString(_ row: [String: JSONValue], _ key: String) throws -> String {
    guard case let .string(value) = row[key], !value.isEmpty, value.count <= 128,
          value.unicodeScalars.allSatisfy(\.isASCII) else { throw dayInvalid() }
    return value
}
private func dayHash(_ row: [String: JSONValue], _ key: String) throws -> String {
    let value = try dayString(row, key)
    guard value.count == 64, value.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw dayInvalid() }
    return value
}
private func dayIdentity(_ row: [String: JSONValue], _ protocolHash: String, _ contextHash: String) throws {
    guard try dayHash(row, "protocolHash") == protocolHash,
          try dayHash(row, "contextProtocolHash") == contextHash else { throw dayInvalid() }
}
private func dayEnum(_ row: [String: JSONValue], _ key: String, _ choices: Set<String>) throws -> String {
    let value = try dayString(row, key)
    guard choices.contains(value) else { throw dayInvalid() }
    return value
}
private func dayArray(_ row: [String: JSONValue], _ key: String, limit: Int) throws -> [JSONValue] {
    guard case let .array(value) = row[key], value.count <= limit else { throw dayInvalid() }
    return value
}
private func dayDate(_ row: [String: JSONValue], _ key: String) throws -> Date {
    let text = try dayString(row, key)
    guard text.hasSuffix("Z") || text.hasSuffix("+00:00") else { throw dayInvalid() }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = formatter.date(from: text) else { throw dayInvalid() }
    return date
}
private func dayDecimal(_ row: [String: JSONValue], _ key: String, minimum: Double, maximum: Double) throws -> String? {
    if case .null = row[key] { return nil }
    let text = try dayString(row, key)
    guard let value = Double(text), value.isFinite, value >= minimum, value <= maximum else { throw dayInvalid() }
    return text
}
