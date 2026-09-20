import Foundation

struct TrendAdvisorSuggestion: Decodable, Equatable, Sendable, Identifiable {
    let symbol: String
    let strategyID: String
    let roundID: String
    let protocolHash: String
    let sourceHash: String
    let candidateHash: String
    let evidenceHash: String
    let policyHash: String
    let posture: String
    let timeframe: String
    let decisionAt: Date
    let availableAt: Date?
    let expiresAt: Date
    let entryLow: String?
    let entryHigh: String?
    let invalidation: String?
    let target: String?
    let reasons: [String]
    let closeReasons: [String]
    var id: String { "\(candidateHash):\(decisionAt.timeIntervalSince1970)" }

    init(from decoder: Decoder) throws { try self.init(value: JSONValue(from: decoder)) }

    init(value: JSONValue) throws {
        guard case let .object(root) = value else { throw advisorInvalid("root") }
        let allowed: Set<String> = ["symbol", "strategyId", "roundId", "protocolHash", "sourceHash", "candidateHash",
            "evidenceHash", "policyHash", "source", "posture", "paperOnly", "qualificationStatus", "timeframe",
            "decisionAt", "availableAt", "expiresAt", "entryLow", "entryHigh", "invalidation", "target", "reasons", "closeReasons"]
        guard Set(root.keys) == allowed, case .bool(true) = root["paperOnly"],
              try advisorString(root, "qualificationStatus") == "unqualified",
              try advisorString(root, "source") == "binance:spot"
        else { throw advisorInvalid("unsupported fields or qualification") }
        symbol = try advisorString(root, "symbol")
        guard ["BTCUSDT", "ETHUSDT"].contains(symbol) else { throw advisorInvalid("symbol") }
        strategyID = try advisorString(root, "strategyId")
        roundID = try advisorString(root, "roundId")
        protocolHash = try advisorHash(root, "protocolHash")
        sourceHash = try advisorHash(root, "sourceHash")
        candidateHash = try advisorHash(root, "candidateHash")
        evidenceHash = try advisorHash(root, "evidenceHash")
        policyHash = try advisorHash(root, "policyHash")
        posture = try advisorString(root, "posture")
        guard ["long_research", "stand_aside"].contains(posture) else { throw advisorInvalid("spot short or unknown posture") }
        timeframe = try advisorString(root, "timeframe")
        guard timeframe == "1m / 5m" else { throw advisorInvalid("timeframe") }
        decisionAt = try advisorDate(root, "decisionAt")
        expiresAt = try advisorDate(root, "expiresAt")
        availableAt = if case .null = root["availableAt"] { nil } else { try advisorDate(root, "availableAt") }
        guard expiresAt.timeIntervalSince(decisionAt) <= 15 else { throw advisorInvalid("causal timestamps") }
        if let availableAt, availableAt > decisionAt { throw advisorInvalid("causal timestamps") }
        if let availableAt, expiresAt.timeIntervalSince(availableAt) > 15 { throw advisorInvalid("evidence expiry") }
        entryLow = try advisorLevel(root, "entryLow")
        entryHigh = try advisorLevel(root, "entryHigh")
        invalidation = try advisorLevel(root, "invalidation")
        target = try advisorLevel(root, "target")
        reasons = try advisorReasons(root, "reasons")
        closeReasons = try advisorReasons(root, "closeReasons")
        guard closeReasons == ["invalidation_reached", "target_reached", "trend_alignment_lost", "evidence_expired"] else {
            throw advisorInvalid("close reasons")
        }
        if posture == "long_research" {
            guard expiresAt > decisionAt, let availableAt, decisionAt.timeIntervalSince(availableAt) < 15,
                  let entryLow, let entryHigh, let invalidation, let target,
                  let low = Double(entryLow), let high = Double(entryHigh), let stop = Double(invalidation), let goal = Double(target),
                  stop < low, low <= high, high < goal, reasons == ["trend_aligned", "candidate_confirmed"]
            else { throw advisorInvalid("long research evidence or levels") }
        } else {
            guard entryLow == nil, entryHigh == nil, invalidation == nil, target == nil else {
                throw advisorInvalid("stand aside cannot have levels")
            }
        }
    }
}

struct TrendAdvisorPresentation: Equatable, Sendable {
    let postureTitle: String
    let showsLevels: Bool
    let reasons: String
    init(suggestion: TrendAdvisorSuggestion, now: Date) {
        let evidenceIsFresh = suggestion.availableAt.map { now >= $0 && now.timeIntervalSince($0) < 15 } ?? false
        showsLevels = suggestion.posture == "long_research" && evidenceIsFresh && now >= suggestion.decisionAt && now < suggestion.expiresAt
        postureTitle = showsLevels ? "Long research posture" : "Stand aside"
        reasons = (now >= suggestion.expiresAt || !evidenceIsFresh ? ["evidence_expired"] :
                    now < suggestion.decisionAt ? ["decision_not_yet_available"] : suggestion.reasons)
            .map { $0.replacingOccurrences(of: "_", with: " ") }.joined(separator: " · ")
    }
}

private func advisorInvalid(_ reason: String) -> SnapshotValidationError {
    .invalidResearchEvidence("Trend Advisor: \(reason)")
}

private func advisorString(_ root: [String: JSONValue], _ key: String) throws -> String {
    guard case let .string(text) = root[key], !text.isEmpty, text.utf8.count <= 256 else { throw advisorInvalid(key) }
    return text
}

private func advisorHash(_ root: [String: JSONValue], _ key: String) throws -> String {
    let text = try advisorString(root, key)
    guard text.utf8.count == 64, text.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw advisorInvalid(key) }
    return text
}

private func advisorDate(_ root: [String: JSONValue], _ key: String) throws -> Date {
    let text = try advisorString(root, key)
    guard text.hasSuffix("Z") || text.hasSuffix("+00:00") else { throw advisorInvalid(key) }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = formatter.date(from: text) else { throw advisorInvalid(key) }
    return date
}

private func advisorLevel(_ root: [String: JSONValue], _ key: String) throws -> String? {
    if case .null = root[key] { return nil }
    let text = try advisorString(root, key)
    guard text.utf8.count <= 64, let value = Double(text), value.isFinite, value > 0 else { throw advisorInvalid(key) }
    return text
}

private func advisorReasons(_ root: [String: JSONValue], _ key: String) throws -> [String] {
    guard case let .array(items) = root[key], !items.isEmpty, items.count <= 16 else { throw advisorInvalid(key) }
    return try items.map { item in
        guard case let .string(text) = item, !text.isEmpty, text.utf8.count <= 256 else { throw advisorInvalid(key) }
        return text
    }
}
