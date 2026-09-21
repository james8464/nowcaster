import Foundation

struct LivePaperSignalState: Equatable, Sendable {
    let kind: String
    let protocolHash: String
    let updatedAt: Date
    let evaluatedAt: Date?
    let reasons: [String]
    let suggestion: TrendAdvisorSuggestion?

    static func decode(_ data: Data, protocolHash expected: String, now: Date) throws -> Self {
        let root = try paperObject(data)
        try paperKeys(root, required: ["kind", "protocolHash", "updatedAt", "evaluatedAt", "reasons", "suggestion"])
        let kind = try paperString(root, "kind")
        let identity = try paperHash(root, "protocolHash")
        let updated = try paperDate(root, "updatedAt")
        let evaluated = try paperOptionalDate(root, "evaluatedAt")
        let reasons = try paperReasons(root, "reasons")
        let suggestion: TrendAdvisorSuggestion? = if case .null = root["suggestion"] { nil }
            else { try TrendAdvisorSuggestion(value: root["suggestion"]!) }
        guard ["stopped", "warming", "abstaining", "published", "stale", "failed"].contains(kind),
              identity == expected, updated <= now, evaluated.map({ $0 <= updated }) ?? true else {
            throw paperInvalid("identity, state or future clock")
        }
        if kind == "published" {
            guard let suggestion, let evaluated, reasons.isEmpty, suggestion.protocolHash == identity,
                  suggestion.posture == "long_research", suggestion.decisionAt <= evaluated,
                  updated < suggestion.expiresAt, now < suggestion.expiresAt,
                  now.timeIntervalSince(updated) < 15 else { throw paperInvalid("expired or unbound publication") }
        } else if suggestion != nil { throw paperInvalid("suggestion outside publication") }
        return Self(kind: kind, protocolHash: identity, updatedAt: updated, evaluatedAt: evaluated,
                    reasons: reasons, suggestion: suggestion)
    }

    func currentSuggestion(now: Date, isRunning: Bool) -> TrendAdvisorSuggestion? {
        guard isRunning, kind == "published", now >= updatedAt, now.timeIntervalSince(updatedAt) < 15,
              let suggestion, TrendAdvisorPresentation(suggestion: suggestion, now: now).showsLevels else { return nil }
        return suggestion
    }
}

struct LivePaperSignalEvent: Equatable, Sendable, Identifiable {
    let kind: String
    let at: Date
    let detail: String?
    let posture: String?
    let candidateHash: String?
    let notificationOutcome: String?
    let id: Int

    static func decodeHistory(_ data: Data, now: Date) throws -> [Self] {
        guard data.count <= 8 * 1024 * 1024, data.isEmpty || data.last == 10,
              let text = String(data: data, encoding: .utf8) else { throw paperInvalid("event history bounds or torn tail") }
        if data.isEmpty { return [] }
        return try text.dropLast().split(separator: "\n", omittingEmptySubsequences: false).enumerated().map { index, line in
            let root = try paperObject(Data(line.utf8))
            try paperKeys(root, required: ["kind", "at"], optional: ["detail", "posture", "candidateHash", "notificationOutcome"])
            let kind = try paperString(root, "kind")
            let at = try paperDate(root, "at")
            guard ["started", "stopped", "provider_health", "gap", "reconnect", "evaluated", "published", "abstaining",
                   "notification_attempt", "notification_outcome"].contains(kind), at <= now else { throw paperInvalid("event kind or future clock") }
            let detail = try paperOptionalString(root, "detail")
            if let detail { try paperResearchText(detail) }
            let posture = try paperOptionalString(root, "posture")
            let candidate = try paperOptionalString(root, "candidateHash")
            let outcome = try paperOptionalString(root, "notificationOutcome")
            if let posture, !["long_research", "stand_aside"].contains(posture) { throw paperInvalid("event posture") }
            if let candidate, !paperIsHash(candidate) { throw paperInvalid("event candidate") }
            if let outcome, !["not_requested", "suppressed", "delivered", "failed"].contains(outcome) { throw paperInvalid("event outcome") }
            if kind == "published", posture != "long_research" || candidate == nil { throw paperInvalid("publication event") }
            if kind == "abstaining", posture != nil && posture != "stand_aside" { throw paperInvalid("abstention event") }
            if kind == "notification_outcome", outcome == nil { throw paperInvalid("missing outcome") }
            if kind != "notification_outcome", outcome != nil { throw paperInvalid("misplaced outcome") }
            return Self(kind: kind, at: at, detail: detail, posture: posture, candidateHash: candidate,
                        notificationOutcome: outcome, id: index)
        }
    }
}

struct LivePaperNotification: Equatable, Sendable {
    let protocolHash: String
    let candidateHash: String
    let materialKey: String
    let symbol: String
    let generatedAt: Date
    let expiresAt: Date
    let title: String
    let body: String

    static func decode(_ data: Data, protocolHash expected: String, now: Date) throws -> Self {
        let notice = try decodeReservation(data, protocolHash: expected)
        guard notice.isFresh(at: now) else { throw paperInvalid("expired or future notification") }
        return notice
    }

    // Retained reservations may expire while macOS handles permission/settings.
    // They still require an outcome record, but never authorize delivery.
    static func decodeReservation(_ data: Data, protocolHash expected: String) throws -> Self {
        let root = try paperObject(data)
        try paperKeys(root, required: ["protocolHash", "candidateHash", "materialKey", "symbol", "generatedAt", "expiresAt",
            "cooldownKey", "paperOnly", "destination", "title", "body"])
        let identity = try paperHash(root, "protocolHash")
        let candidate = try paperHash(root, "candidateHash")
        let material = try paperHash(root, "materialKey")
        let symbol = try paperString(root, "symbol")
        let generated = try paperDate(root, "generatedAt")
        let expires = try paperDate(root, "expiresAt")
        let title = try paperString(root, "title")
        let body = try paperString(root, "body")
        let rawExpiry = try paperString(root, "expiresAt")
        guard identity == expected, ["BTCUSDT", "ETHUSDT"].contains(symbol), case .bool(true) = root["paperOnly"],
              try paperString(root, "cooldownKey") == identity + ":" + symbol,
              try paperString(root, "destination") == "strategy_lab_evidence", title == "Nowcaster paper research",
              body == "Paper-only research posture — not a trade instruction. \(symbol). Expires \(rawExpiry.replacingOccurrences(of: "+00:00", with: "Z")).",
              generated < expires, expires.timeIntervalSince(generated) <= 15 else {
            throw paperInvalid("notification boundary")
        }
        return Self(protocolHash: identity, candidateHash: candidate, materialKey: material, symbol: symbol,
                    generatedAt: generated, expiresAt: expires, title: title, body: body)
    }

    func isFresh(at now: Date) -> Bool { generatedAt <= now && now < expiresAt }
}

private func paperInvalid(_ reason: String) -> SnapshotValidationError {
    .invalidResearchEvidence("Live paper signals: \(reason)")
}

private func paperObject(_ data: Data) throws -> [String: JSONValue] {
    guard data.count <= 64 * 1024 else { throw paperInvalid("payload too large") }
    guard case let .object(root) = try JSONDecoder.nowcaster.decode(JSONValue.self, from: data) else { throw paperInvalid("root") }
    return root
}

private func paperKeys(_ root: [String: JSONValue], required: Set<String>, optional: Set<String> = []) throws {
    guard required.isSubset(of: Set(root.keys)), Set(root.keys).isSubset(of: required.union(optional)) else {
        throw paperInvalid("unsupported or missing fields")
    }
}

private func paperString(_ root: [String: JSONValue], _ key: String) throws -> String {
    guard case let .string(value) = root[key], !value.isEmpty, value.utf8.count <= 512 else { throw paperInvalid(key) }
    return value
}

private func paperOptionalString(_ root: [String: JSONValue], _ key: String) throws -> String? {
    guard let value = root[key] else { return nil }
    if case .null = value { return nil }
    return try paperString(root, key)
}

private func paperIsHash(_ value: String) -> Bool { value.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) } }
private func paperHash(_ root: [String: JSONValue], _ key: String) throws -> String {
    let value = try paperString(root, key)
    guard paperIsHash(value) else { throw paperInvalid(key) }
    return value
}

private func paperDate(_ root: [String: JSONValue], _ key: String) throws -> Date {
    let value = try paperString(root, key)
    guard value.hasSuffix("Z") || value.hasSuffix("+00:00") else { throw paperInvalid(key) }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = formatter.date(from: value) else { throw paperInvalid(key) }
    return date
}

private func paperOptionalDate(_ root: [String: JSONValue], _ key: String) throws -> Date? {
    if case .null = root[key] { return nil }
    return try paperDate(root, key)
}

private func paperResearchText(_ text: String) throws {
    let words = Set(text.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted))
    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          words.isDisjoint(with: ["buy", "sell", "order", "broker", "execute", "execution", "position", "trade"]) else {
        throw paperInvalid("action-shaped text")
    }
}

private func paperReasons(_ root: [String: JSONValue], _ key: String) throws -> [String] {
    guard case let .array(items) = root[key], items.count <= 16 else { throw paperInvalid(key) }
    return try items.map {
        guard case let .string(text) = $0, text.utf8.count <= 512 else { throw paperInvalid(key) }
        try paperResearchText(text)
        return text
    }
}
