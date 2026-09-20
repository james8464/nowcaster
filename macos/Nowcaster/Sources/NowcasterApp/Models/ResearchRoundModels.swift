import Foundation

struct ResearchRoundProviderHealth: Equatable, Sendable {
    let provider: String
    let feed: String
    let revision: String
    let reportedAt: Date
    let lastSuccessfulObservationAt: Date?
    let maximumAgeSeconds: Int
    let state: String
    let exclusions: [String]

    init(value: JSONValue) throws {
        guard case let .object(values) = value else { throw invalid("provider health") }
        try validateKeys(values, allowed: ["provider", "feed", "revision", "reportedAt",
            "lastSuccessfulObservationAt", "maximumAgeSeconds", "state", "exclusions"], context: "provider health")
        provider = try requiredString(values, "provider", context: "provider health")
        feed = try requiredString(values, "feed", context: "provider health")
        revision = try requiredString(values, "revision", context: "provider health")
        state = try requiredString(values, "state", context: "provider health")
        exclusions = try reasonList(values, "exclusions", context: "provider health")
        guard provider == "binance", feed == "spot",
              ["healthy", "degraded", "stale", "error", "unavailable"].contains(state),
              case let .number(age) = try requiredValue(values, "maximumAgeSeconds", context: "provider health"),
              age.isFinite, age.rounded() == age, (1 ... 86_400).contains(age)
        else { throw invalid("provider health identity or bounds") }
        maximumAgeSeconds = Int(age)
        reportedAt = try healthDate(values, "reportedAt")
        let last = try requiredValue(values, "lastSuccessfulObservationAt", context: "provider health")
        if case .null = last { lastSuccessfulObservationAt = nil }
        else { lastSuccessfulObservationAt = try healthDate(values, "lastSuccessfulObservationAt") }
        if let last = lastSuccessfulObservationAt, last > reportedAt { throw invalid("future provider success") }
        if state == "healthy" {
            guard let last = lastSuccessfulObservationAt, exclusions.isEmpty,
                  reportedAt.timeIntervalSince(last) <= Double(maximumAgeSeconds)
            else { throw invalid("healthy provider lacks fresh evidence") }
        }
        if state == "unavailable", lastSuccessfulObservationAt != nil { throw invalid("unavailable provider success") }
    }

    func title(now: Date) -> String {
        if now < reportedAt { return "Unavailable — report time is in the future" }
        if state == "error" { return "Feed error" }
        guard let last = lastSuccessfulObservationAt else { return "Unavailable" }
        if now.timeIntervalSince(last) > Double(maximumAgeSeconds)
            || now.timeIntervalSince(reportedAt) > Double(maximumAgeSeconds) { return "Stale" }
        return ["healthy": "Healthy", "degraded": "Degraded", "stale": "Stale"][state] ?? "Unavailable"
    }
}

enum ResearchRoundDirection: String, Equatable, Sendable {
    case long
    case abstain
}

enum ResearchRoundCandidateStatus: String, Equatable, Sendable {
    case insufficientData = "insufficient_data"
    case rejected
    case experimentalPaperOnly = "experimental_paper_only"
}

struct ResearchRoundSealedMetrics: Equatable, Sendable {
    let netReturn: String
    let stressedNetReturn: String
    let lowerEdge: String?
    let tradeCount: Int
    let maximumDrawdown: String
    let coverage: String
}

struct ResearchRoundCandidate: Identifiable, Equatable, Sendable {
    let symbol: String
    let strategyID: String
    let candidateHash: String?
    let direction: ResearchRoundDirection
    let status: ResearchRoundCandidateStatus
    let reasons: [String]
    let sealedMetrics: ResearchRoundSealedMetrics

    var id: String { "\(symbol):\(strategyID):\(direction.rawValue)" }
}

struct ResearchRoundSnapshot: Decodable, Equatable, Sendable {
    let roundID: String
    let protocolHash: String
    let status: ResearchRoundCandidateStatus
    let paperOnly: Bool
    let qualificationStatus: String
    let reasons: [String]
    let candidates: [ResearchRoundCandidate]
    let trendAdvisor: [TrendAdvisorSuggestion]
    let providerHealth: ResearchRoundProviderHealth

    init(from decoder: Decoder) throws {
        let value = try JSONValue(from: decoder)
        guard case let .object(root) = value else { throw invalid("round root") }
        try validateKeys(root, allowed: [
            "roundId", "protocolHash", "status", "paperOnly", "qualificationStatus", "reasons", "candidates", "trendAdvisor", "providerHealth",
        ], context: "round")

        roundID = try requiredString(root, "roundId", context: "round")
        protocolHash = try requiredString(root, "protocolHash", context: "round")
        status = try candidateStatus(root, "status", context: "round")
        paperOnly = try requiredBool(root, "paperOnly", context: "round")
        qualificationStatus = try requiredString(root, "qualificationStatus", context: "round")
        reasons = try reasonList(root, "reasons", context: "round")
        candidates = try candidateList(root)
        providerHealth = try ResearchRoundProviderHealth(value: requiredValue(root, "providerHealth", context: "round"))
        if let raw = root["trendAdvisor"] {
            guard case let .array(items) = raw, items.count <= 100 else { throw invalid("trend advisor bounds") }
            trendAdvisor = try items.map { try TrendAdvisorSuggestion(value: $0) }
        } else {
            trendAdvisor = []
        }
        try validate()
    }

    func validate() throws {
        guard paperOnly, qualificationStatus == "unqualified" else {
            throw invalid("Research Round 2 must remain unqualified paper research")
        }
        guard protocolHash.count == 64, protocolHash.allSatisfy(\ .isHexDigit), protocolHash == protocolHash.lowercased() else {
            throw invalid("round protocol identity")
        }
        guard !roundID.isEmpty, roundID.utf8.count <= 256, candidates.count <= 100 else {
            throw invalid("round bounds")
        }
        guard Set(trendAdvisor.map(\.id)).count == trendAdvisor.count else { throw invalid("duplicate advisor decisions") }
        for advisor in trendAdvisor {
            guard advisor.roundID == roundID, advisor.protocolHash == protocolHash else { throw invalid("advisor identity") }
            if advisor.posture == "long_research" {
                guard status == .experimentalPaperOnly, candidates.contains(where: {
                    $0.symbol == advisor.symbol && $0.strategyID == advisor.strategyID && $0.direction == .long
                        && $0.status == .experimentalPaperOnly && $0.candidateHash == advisor.candidateHash
                }) else { throw invalid("advisor candidate is unavailable") }
            }
        }
    }
}

struct ResearchRoundPresentation: Equatable, Sendable {
    let title = "Research Round 2 — paper-only"
    let subtitle = "Simulated evidence, not a trade instruction or proof of profit."
    let statusTitle: String
    let providerHealthTitle: String
    let abstentionTitle: String?

    init(snapshot: ResearchRoundSnapshot?, now: Date = Date()) {
        guard let snapshot else {
            statusTitle = "No research report loaded"
            providerHealthTitle = "No provider report loaded"
            abstentionTitle = "Load a retained Research Round 2 summary to view its evidence."
            return
        }
        statusTitle = switch snapshot.status {
        case .experimentalPaperOnly: "Experimental paper-only evidence"
        case .insufficientData: "Insufficient data — stand aside"
        case .rejected: "Rejected — stand aside"
        }
        providerHealthTitle = snapshot.providerHealth.title(now: now)
        abstentionTitle = snapshot.status == .experimentalPaperOnly ? nil : snapshot.reasons.joined(separator: " · ")
    }
}

struct ResearchRoundCandidatePresentation: Equatable, Sendable {
    let statusTitle: String
    let directionTitle: String
    let reasonTitle: String
    let systemImage: String

    init(candidate: ResearchRoundCandidate) {
        statusTitle = switch candidate.status {
        case .experimentalPaperOnly: "Experimental paper-only"
        case .insufficientData: "Insufficient data"
        case .rejected: "Rejected"
        }
        if candidate.status == .experimentalPaperOnly, candidate.direction == .long {
            directionTitle = "Long research only"
            systemImage = "flask"
        } else {
            directionTitle = "Stand aside"
            systemImage = "pause.circle"
        }
        reasonTitle = candidate.reasons.isEmpty
            ? "No retained reason was published."
            : candidate.reasons.map { $0.replacingOccurrences(of: "_", with: " ") }.joined(separator: " · ")
    }
}

private let researchRoundActionTerms: Set<String> = [
    "order", "notification", "alert", "lifecycle", "position", "broker", "execution", "setup",
]

private func invalid(_ detail: String) -> SnapshotValidationError {
    .invalidResearchEvidence("Research Round 2: \(detail)")
}

private func validateKeys(_ values: [String: JSONValue], allowed: Set<String>, context: String) throws {
    guard values.keys.allSatisfy({ allowed.contains($0) }) else { throw invalid("\(context) contains an unsupported field") }
    for key in values.keys {
        let normalized = key.lowercased().replacingOccurrences(of: "-", with: "_")
        guard !researchRoundActionTerms.contains(where: { normalized.contains($0) }) else {
            throw invalid("\(context) contains an action-shaped field")
        }
    }
}

private func requiredValue(_ values: [String: JSONValue], _ key: String, context: String) throws -> JSONValue {
    guard let value = values[key] else { throw invalid("\(context) is missing \(key)") }
    return value
}

private func requiredString(_ values: [String: JSONValue], _ key: String, context: String) throws -> String {
    guard case let .string(value) = try requiredValue(values, key, context: context),
          !value.isEmpty, value.utf8.count <= 256
    else { throw invalid("\(context) \(key)") }
    return value
}

private func requiredBool(_ values: [String: JSONValue], _ key: String, context: String) throws -> Bool {
    guard case let .bool(value) = try requiredValue(values, key, context: context) else { throw invalid("\(context) \(key)") }
    return value
}

private func healthDate(_ values: [String: JSONValue], _ key: String) throws -> Date {
    let text = try requiredString(values, key, context: "provider health")
    guard text.hasSuffix("Z") || text.hasSuffix("+00:00") else { throw invalid("provider health UTC timestamp") }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = formatter.date(from: text) else { throw invalid("provider health timestamp") }
    return date
}

private func candidateStatus(_ values: [String: JSONValue], _ key: String, context: String) throws -> ResearchRoundCandidateStatus {
    guard let status = ResearchRoundCandidateStatus(rawValue: try requiredString(values, key, context: context)) else {
        throw invalid("\(context) status")
    }
    return status
}

private func reasonList(_ values: [String: JSONValue], _ key: String, context: String) throws -> [String] {
    guard case let .array(items) = try requiredValue(values, key, context: context), items.count <= 16 else {
        throw invalid("\(context) reasons")
    }
    let strings = items.compactMap(\ .stringValue)
    guard strings.count == items.count, strings.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 256 }) else {
        throw invalid("\(context) reasons")
    }
    return strings
}

private func candidateList(_ values: [String: JSONValue]) throws -> [ResearchRoundCandidate] {
    guard case let .array(items) = try requiredValue(values, "candidates", context: "round"), items.count <= 100 else {
        throw invalid("round candidates")
    }
    return try items.map { item in
        guard case let .object(candidate) = item else { throw invalid("candidate") }
        try validateKeys(candidate, allowed: [
            "symbol", "strategyId", "direction", "status", "paperOnly", "qualificationStatus", "reasons", "sealedMetrics", "candidateHash",
        ], context: "candidate")
        let directionValue = try requiredString(candidate, "direction", context: "candidate")
        guard let direction = ResearchRoundDirection(rawValue: directionValue) else { throw invalid("spot short or direction") }
        guard try requiredBool(candidate, "paperOnly", context: "candidate"),
              try requiredString(candidate, "qualificationStatus", context: "candidate") == "unqualified"
        else { throw invalid("candidate qualification") }
        return ResearchRoundCandidate(
            symbol: try requiredSymbol(candidate),
            strategyID: try requiredString(candidate, "strategyId", context: "candidate"),
            candidateHash: try candidateIdentity(candidate),
            direction: direction,
            status: try candidateStatus(candidate, "status", context: "candidate"),
            reasons: try reasonList(candidate, "reasons", context: "candidate"),
            sealedMetrics: try sealedMetrics(candidate)
        )
    }
}

private func candidateIdentity(_ values: [String: JSONValue]) throws -> String? {
    guard values["candidateHash"] != nil else { return nil }
    let identity = try requiredString(values, "candidateHash", context: "candidate")
    guard identity.utf8.count == 64, identity.allSatisfy({ "0123456789abcdef".contains($0) }) else {
        throw invalid("candidate identity")
    }
    return identity
}

private func requiredSymbol(_ values: [String: JSONValue]) throws -> String {
    let symbol = try requiredString(values, "symbol", context: "candidate")
    guard ["BTCUSDT", "ETHUSDT"].contains(symbol) else { throw invalid("candidate symbol") }
    return symbol
}

private func sealedMetrics(_ values: [String: JSONValue]) throws -> ResearchRoundSealedMetrics {
    guard case let .object(metrics) = try requiredValue(values, "sealedMetrics", context: "candidate") else {
        throw invalid("sealed metrics")
    }
    try validateKeys(metrics, allowed: [
        "netReturn", "stressedNetReturn", "lowerEdge", "tradeCount", "maximumDrawdown", "coverage",
    ], context: "sealed metrics")
    let netReturn = try requiredNumericString(metrics, "netReturn")
    let stressedNetReturn = try requiredNumericString(metrics, "stressedNetReturn")
    let lowerEdge = try numericString(metrics, "lowerEdge", nullable: true)
    let maximumDrawdown = try requiredNumericString(metrics, "maximumDrawdown")
    let coverage = try requiredNumericString(metrics, "coverage")
    guard let drawdown = Double(maximumDrawdown), (0 ... 1).contains(drawdown),
          let coverageValue = Double(coverage), (0 ... 1).contains(coverageValue),
          case let .number(tradeCountValue) = try requiredValue(metrics, "tradeCount", context: "sealed metrics"),
          tradeCountValue.isFinite, tradeCountValue.rounded() == tradeCountValue, tradeCountValue >= 0, tradeCountValue <= 1_000_000
    else { throw invalid("sealed metric bounds") }
    return ResearchRoundSealedMetrics(
        netReturn: netReturn,
        stressedNetReturn: stressedNetReturn,
        lowerEdge: lowerEdge,
        tradeCount: Int(tradeCountValue),
        maximumDrawdown: maximumDrawdown,
        coverage: coverage
    )
}

private func numericString(_ values: [String: JSONValue], _ key: String, nullable: Bool) throws -> String? {
    let value = try requiredValue(values, key, context: "sealed metrics")
    if nullable, case .null = value { return nil }
    guard case let .string(string) = value, string.utf8.count <= 64,
          let number = Double(string), number.isFinite
    else { throw invalid("sealed metric \(key)") }
    return string
}

private func requiredNumericString(_ values: [String: JSONValue], _ key: String) throws -> String {
    guard let value = try numericString(values, key, nullable: false) else {
        throw invalid("sealed metric \(key)")
    }
    return value
}
