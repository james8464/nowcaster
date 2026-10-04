import Foundation

struct WorkflowFields: Equatable, Sendable {
    let values: [String: JSONValue]
    func string(_ key: String) -> String? {
        if case let .string(value) = values[key] { return value }
        return nil
    }
    func date(_ key: String) -> Date? { string(key).flatMap(workflowDate) }
    func decimal(_ key: String) -> Decimal? { string(key).flatMap { Decimal(string: $0, locale: Locale(identifier: "en_US_POSIX")) } }
    func count(_ key: String) -> Int {
        if case let .number(value) = values[key] { return Int(value) }
        return 0
    }
    func fields(_ key: String) -> WorkflowFields? {
        if case let .object(value) = values[key] { return WorkflowFields(values: value) }
        return nil
    }
    func rows(_ key: String) -> [WorkflowFields] {
        if case let .array(value) = values[key] {
            return value.compactMap { if case let .object(row) = $0 { WorkflowFields(values: row) } else { nil } }
        }
        return []
    }
    var reasons: [String] {
        if case let .array(value) = values["reasons"] {
            return value.compactMap { if case let .string(reason) = $0 { reason } else { nil } }
        }
        return []
    }
}

struct DiagnosticWorkflow: Equatable, Sendable {
    let state: String
    let policyHash: String
    let updatedAt: Date?
    let reasons: [String]
    let account: WorkflowFields?
    let decisions: [WorkflowFields]
    let positions: [WorkflowFields]
    let recentTrades: [WorkflowFields]
    let review: WorkflowFields?

    static func decode(_ data: Data, protocolHash: String, policyHash: String? = nil, now: Date) throws -> Self {
        guard data.count <= 65_536, case let .object(root) = try JSONDecoder().decode(JSONValue.self, from: data) else { throw workflowInvalid() }
        let row = WorkflowFields(values: root)
        let validation = WorkflowValidation(protocolHash: protocolHash, policyHash: try workflowHash(row, "policyHash"), now: now)
        try validation.top(row)
        guard policyHash == nil || validation.policyHash == policyHash else { throw workflowInvalid() }
        return Self(state: row.string("state")!, policyHash: validation.policyHash, updatedAt: row.date("updatedAt"),
                    reasons: row.reasons, account: row.fields("account"), decisions: row.rows("decisions"),
                    positions: row.rows("positions"), recentTrades: row.rows("recentTrades"), review: row.fields("review"))
    }
}

private func workflowInvalid() -> SnapshotValidationError {
    .invalidResearchEvidence("Diagnostic simulator evidence is unavailable or failed validation.")
}
private func workflowDate(_ text: String) -> Date? {
    guard text.hasSuffix("Z") else { return nil }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: text)
}
private func workflowHash(_ row: WorkflowFields, _ key: String) throws -> String {
    let value = try workflowString(row, key)
    guard value.count == 64, value.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw workflowInvalid() }
    return value
}
private func workflowString(_ row: WorkflowFields, _ key: String) throws -> String {
    guard let value = row.string(key), !value.isEmpty, value.utf8.count <= 512,
          !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { throw workflowInvalid() }
    return value
}

private struct WorkflowValidation {
    let protocolHash: String
    let policyHash: String
    let now: Date
    func keys(_ row: WorkflowFields, _ keys: String) throws {
        guard Set(row.values.keys) == Set(keys.split(separator: " ").map(String.init)) else { throw workflowInvalid() }
    }
    func identity(_ row: WorkflowFields, source: String? = nil) throws {
        guard try workflowHash(row, "protocolHash") == protocolHash,
              try workflowHash(row, "policyHash") == policyHash else { throw workflowInvalid() }
        if let source { guard try workflowHash(row, "sourceIdentityHash") == source else { throw workflowInvalid() } }
    }
    func choice(_ row: WorkflowFields, _ key: String, _ choices: Set<String>, nullable: Bool = false) throws {
        if nullable, row.values[key] == .null { return }
        guard choices.contains(try workflowString(row, key)) else { throw workflowInvalid() }
    }
    func time(_ row: WorkflowFields, _ key: String, nullable: Bool = false, future: TimeInterval = 0) throws {
        if nullable, row.values[key] == .null { return }
        guard let date = row.date(key), date <= now.addingTimeInterval(future) else { throw workflowInvalid() }
    }
    func amount(_ row: WorkflowFields, _ key: String, minimum: Decimal = 0, positive: Bool = false, maximum: Decimal = Decimal(string: "1e30")!, nullable: Bool = false) throws {
        if nullable, row.values[key] == .null { return }
        let text = try workflowString(row, key)
        guard text.range(of: #"^-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"#, options: .regularExpression) != nil,
              let double = Double(text), double.isFinite,
              let value = row.decimal(key), !value.isNaN, value >= minimum, value <= maximum,
              !positive || value > 0 else { throw workflowInvalid() }
    }
    func counts(_ row: WorkflowFields, _ keys: [String]) throws {
        for key in keys {
            guard case let .number(value) = row.values[key], value.isFinite, value >= 0,
                  value.rounded() == value, value <= 1_000_000_000 else { throw workflowInvalid() }
        }
    }
    func array(_ row: WorkflowFields, _ key: String, limit: Int) throws -> [WorkflowFields] {
        guard case let .array(items) = row.values[key], items.count <= limit else { throw workflowInvalid() }
        return try items.map { guard case let .object(fields) = $0 else { throw workflowInvalid() }; return WorkflowFields(values: fields) }
    }
    func reasons(_ row: WorkflowFields) throws {
        guard case let .array(items) = row.values["reasons"], items.count <= 64 else { throw workflowInvalid() }
        for item in items {
            guard case let .string(text) = item, !text.isEmpty, text.count <= 128,
                  text.allSatisfy({ "abcdefghijklmnopqrstuvwxyz0123456789_".contains($0) }) else { throw workflowInvalid() }
        }
    }
    func optionalString(_ row: WorkflowFields, _ key: String, hash: Bool = false) throws {
        if row.values[key] != .null {
            if hash { _ = try workflowHash(row, key) } else { _ = try workflowString(row, key) }
        }
    }
    func top(_ row: WorkflowFields) throws {
        try keys(row, "schemaVersion paperOnly protocolHash policyHash updatedAt state reasons decisions account positions recentTrades review")
        guard row.values["schemaVersion"] == .number(1), row.values["paperOnly"] == .bool(true) else { throw workflowInvalid() }
        try identity(row); try time(row, "updatedAt", nullable: true); try reasons(row)
        try choice(row, "state", ["disabled", "watching", "pending_entry", "position_open", "pending_exit", "limited", "stale", "error"])
        let decisions = try array(row, "decisions", limit: 2), positions = try array(row, "positions", limit: 1)
        let trades = try array(row, "recentTrades", limit: 20)
        if ["disabled", "error"].contains(row.string("state")!) {
            guard row.values["account"] == .null, row.values["review"] == .null, row.values["updatedAt"] == .null,
                  decisions.isEmpty, positions.isEmpty, trades.isEmpty else { throw workflowInvalid() }
            return
        }
        guard let account = row.fields("account"), let review = row.fields("review"), row.date("updatedAt") != nil else { throw workflowInvalid() }
        let source = try workflowHash(account, "sourceIdentityHash")
        try validateAccount(account, source: source)
        for item in decisions { try decision(item, source: source) }
        guard Set(decisions.compactMap { $0.string("symbol") }).count == decisions.count else { throw workflowInvalid() }
        for item in positions { try position(item, source: source) }
        for item in trades { try outcome(item) }
        guard trades.compactMap({ $0.date("exitAt") }) == trades.compactMap({ $0.date("exitAt") }).sorted() else { throw workflowInvalid() }
        try validateReview(review, account: account)
        let pending = account.fields("pendingEntry") != nil, exiting = account.fields("pendingExit") != nil
        guard !(pending && !positions.isEmpty), !exiting || !positions.isEmpty else { throw workflowInvalid() }
        switch row.string("state") {
        case "pending_entry": guard pending, positions.isEmpty else { throw workflowInvalid() }
        case "position_open": guard !positions.isEmpty, !exiting else { throw workflowInvalid() }
        case "pending_exit": guard exiting else { throw workflowInvalid() }
        case "watching": guard !pending, positions.isEmpty else { throw workflowInvalid() }
        case "limited": guard positions.isEmpty else { throw workflowInvalid() }
        default: break // Stale preserves only historical context, including pending exits.
        }
    }
    func decision(_ row: WorkflowFields, source: String) throws {
        try keys(row, "symbol status reasons decisionAt expiresAt protocolHash policyHash sourceIdentityHash sourceKey observationAt observationHash contextHash session calendarAvailable rankScore setup triggerLevel entry stop target")
        try identity(row, source: source); try reasons(row)
        try choice(row, "symbol", ["BTCUSDT", "ETHUSDT"]); try choice(row, "status", ["ready", "watching", "blocked"])
        try time(row, "decisionAt"); try time(row, "expiresAt", future: 60); try time(row, "observationAt", nullable: true)
        guard let decisionAt = row.date("decisionAt"), let expiresAt = row.date("expiresAt"),
              expiresAt >= decisionAt, expiresAt.timeIntervalSince(decisionAt) <= 60,
              row.date("observationAt").map({ $0 <= decisionAt }) ?? true else { throw workflowInvalid() }
        for key in ["observationHash", "contextHash"] { try optionalString(row, key, hash: true) }
        try optionalString(row, "sourceKey")
        try choice(row, "session", ["asia", "europe", "europe_americas_overlap", "americas", "overnight"], nullable: true)
        guard row.values["calendarAvailable"] == .bool(true) || row.values["calendarAvailable"] == .bool(false) else { throw workflowInvalid() }
        try choice(row, "setup", ["breakout", "pullback_reclaim"], nullable: true)
        try amount(row, "rankScore", nullable: true)
        for key in ["triggerLevel", "entry", "stop", "target"] { try amount(row, key, positive: true, nullable: true) }
        if row.string("status") == "ready" {
            guard row.reasons.isEmpty, row.values["calendarAvailable"] == .bool(true), row.string("setup") != nil,
                  row.string("sourceKey") != nil, row.date("observationAt") != nil,
                  row.string("observationHash") != nil, row.string("contextHash") != nil,
                  row.decimal("triggerLevel") != nil, let entry = row.decimal("entry"),
                  let stop = row.decimal("stop"), let target = row.decimal("target"), stop < entry, entry < target else { throw workflowInvalid() }
        }
    }
    func validateAccount(_ row: WorkflowFields, source: String) throws {
        try keys(row, "schemaVersion policyHash protocolHash sourceIdentityHash activatedAt lastAt utcDay cash equity unrealizedPnl valuationAt realizedPnl realizedLosses fees slippageCost peakEquity maximumDrawdown dailyLoss dailyEntries consecutiveLosses cooldownUntil totalEntries completedTrades totalWins totalLosses pendingEntry pendingExit lastObservationHash lastConsumedQuoteKey lastConsumedQuoteAt lastConsumedQuoteQuantity")
        guard row.values["schemaVersion"] == .number(1) else { throw workflowInvalid() }
        try identity(row, source: source); try time(row, "activatedAt"); try time(row, "lastAt")
        try time(row, "valuationAt", nullable: true); try time(row, "cooldownUntil", nullable: true, future: 3600)
        try time(row, "lastConsumedQuoteAt", nullable: true)
        guard let activated = row.date("activatedAt"), let last = row.date("lastAt"), activated <= last,
              row.date("valuationAt").map({ $0 <= last }) ?? true,
              row.date("lastConsumedQuoteAt").map({ $0 <= last }) ?? true,
              row.string("utcDay") == String(ISO8601DateFormatter().string(from: last).prefix(10)) else { throw workflowInvalid() }
        for key in ["cash", "equity", "realizedLosses", "fees", "slippageCost", "dailyLoss"] { try amount(row, key) }
        for key in ["unrealizedPnl", "realizedPnl"] { try amount(row, key, minimum: -Decimal(string: "1e30")!) }
        try amount(row, "peakEquity", positive: true); try amount(row, "maximumDrawdown", maximum: 1)
        try counts(row, ["dailyEntries", "consecutiveLosses", "totalEntries", "completedTrades", "totalWins", "totalLosses"])
        guard row.count("totalWins") + row.count("totalLosses") <= row.count("completedTrades"),
              row.count("completedTrades") <= row.count("totalEntries"), row.count("dailyEntries") <= row.count("totalEntries") else { throw workflowInvalid() }
        try optionalString(row, "lastObservationHash", hash: true); try optionalString(row, "lastConsumedQuoteKey")
        try amount(row, "lastConsumedQuoteQuantity", positive: true, nullable: true)
        let quoteFields = ["lastConsumedQuoteKey", "lastConsumedQuoteAt", "lastConsumedQuoteQuantity"].map { row.values[$0] == .null }
        guard Set(quoteFields).count == 1 else { throw workflowInvalid() }
        if let pending = row.fields("pendingEntry") {
            try decision(pending, source: source)
            guard pending.string("status") == "ready" else { throw workflowInvalid() }
        } else if row.values["pendingEntry"] != .null { throw workflowInvalid() }
        if let exit = row.fields("pendingExit") {
            try keys(exit, "reason triggeredAt sourceKey"); try time(exit, "triggeredAt"); try optionalString(exit, "sourceKey")
            _ = try workflowString(exit, "reason")
            guard exit.date("triggeredAt")! <= last else { throw workflowInvalid() }
        } else if row.values["pendingExit"] != .null { throw workflowInvalid() }
    }
    func position(_ row: WorkflowFields, source: String) throws {
        try keys(row, "origin entryAt entrySourceKey entryQuoteKey initialQuantity quantity entryPrice entryFee entrySlippage unitDebit initialRisk stop target realizedPnl exitNotional exitFees exitSlippage")
        guard let origin = row.fields("origin") else { throw workflowInvalid() }
        try decision(origin, source: source); try time(row, "entryAt")
        for key in ["entrySourceKey", "entryQuoteKey"] { _ = try workflowString(row, key) }
        for key in ["initialQuantity", "quantity", "entryPrice", "unitDebit", "initialRisk", "stop", "target"] { try amount(row, key, positive: true) }
        for key in ["entryFee", "entrySlippage", "exitNotional", "exitFees", "exitSlippage"] { try amount(row, key) }
        try amount(row, "realizedPnl", minimum: -Decimal(string: "1e30")!)
        guard origin.string("status") == "ready", row.date("entryAt")! > origin.date("decisionAt")!,
              row.date("entryAt")! <= origin.date("expiresAt")!,
              row.decimal("quantity")! <= row.decimal("initialQuantity")!, row.decimal("stop")! >= origin.decimal("stop")!,
              row.decimal("target") == origin.decimal("target") else { throw workflowInvalid() }
    }
    func outcome(_ row: WorkflowFields) throws {
        try keys(row, "decisionId symbol setup entryAt exitAt entrySourceKey exitSourceKey quantity entryPrice exitPrice fees slippageCost netPnl netReturn reason")
        _ = try workflowHash(row, "decisionId"); try choice(row, "symbol", ["BTCUSDT", "ETHUSDT"])
        try choice(row, "setup", ["breakout", "pullback_reclaim"])
        try time(row, "entryAt"); try time(row, "exitAt")
        guard row.date("entryAt")! < row.date("exitAt")! else { throw workflowInvalid() }
        for key in ["entrySourceKey", "exitSourceKey", "reason"] { _ = try workflowString(row, key) }
        for key in ["quantity", "entryPrice", "exitPrice"] { try amount(row, key, positive: true) }
        for key in ["fees", "slippageCost"] { try amount(row, key) }
        for key in ["netPnl", "netReturn"] { try amount(row, key, minimum: -Decimal(string: "1e30")!) }
    }
    func validateReview(_ row: WorkflowFields, account: WorkflowFields) throws {
        try keys(row, "completedTrades wins losses netPnl fees maximumDrawdown setups")
        try counts(row, ["completedTrades", "wins", "losses"])
        try amount(row, "netPnl", minimum: -Decimal(string: "1e30")!); try amount(row, "fees"); try amount(row, "maximumDrawdown", maximum: 1)
        guard row.count("completedTrades") == account.count("completedTrades"), row.count("wins") == account.count("totalWins"),
              row.count("losses") == account.count("totalLosses"), row.decimal("netPnl") == account.decimal("realizedPnl"),
              row.decimal("fees") == account.decimal("fees"), row.decimal("maximumDrawdown") == account.decimal("maximumDrawdown") else { throw workflowInvalid() }
        let setups = try array(row, "setups", limit: 2)
        for setup in setups {
            try keys(setup, "setup completed wins losses netPnl fees"); try choice(setup, "setup", ["breakout", "pullback_reclaim"])
            try counts(setup, ["completed", "wins", "losses"]); try amount(setup, "netPnl", minimum: -Decimal(string: "1e30")!); try amount(setup, "fees")
            guard setup.count("wins") + setup.count("losses") <= setup.count("completed") else { throw workflowInvalid() }
        }
        guard Set(setups.compactMap { $0.string("setup") }).count == setups.count,
              setups.reduce(0, { $0 + $1.count("completed") }) == row.count("completedTrades") else { throw workflowInvalid() }
    }
}
