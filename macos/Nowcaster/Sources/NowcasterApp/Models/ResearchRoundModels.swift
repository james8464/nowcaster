import Foundation

enum ResearchRoundProviderHealth: String, Equatable, Sendable {
    case notPublished = "not_published"

    var title: String { "Not published by this report" }
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
    let providerHealth: ResearchRoundProviderHealth = .notPublished

    init(from decoder: Decoder) throws {
        let value = try JSONValue(from: decoder)
        guard case let .object(root) = value else { throw invalid("round root") }
        try validateKeys(root, allowed: [
            "roundId", "protocolHash", "status", "paperOnly", "qualificationStatus", "reasons", "candidates",
        ], context: "round")

        roundID = try requiredString(root, "roundId", context: "round")
        protocolHash = try requiredString(root, "protocolHash", context: "round")
        status = try candidateStatus(root, "status", context: "round")
        paperOnly = try requiredBool(root, "paperOnly", context: "round")
        qualificationStatus = try requiredString(root, "qualificationStatus", context: "round")
        reasons = try reasonList(root, "reasons", context: "round")
        candidates = try candidateList(root)
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
    }
}

struct ResearchRoundPresentation: Equatable, Sendable {
    let title = "Research Round 2 — paper-only"
    let subtitle = "Simulated evidence, not a trade instruction or proof of profit."
    let statusTitle: String
    let providerHealthTitle: String
    let abstentionTitle: String?

    init(snapshot: ResearchRoundSnapshot?) {
        guard let snapshot else {
            statusTitle = "No research report loaded"
            providerHealthTitle = ResearchRoundProviderHealth.notPublished.title
            abstentionTitle = "Load a retained Research Round 2 summary to view its evidence."
            return
        }
        statusTitle = switch snapshot.status {
        case .experimentalPaperOnly: "Experimental paper-only evidence"
        case .insufficientData: "Insufficient data — stand aside"
        case .rejected: "Rejected — stand aside"
        }
        providerHealthTitle = snapshot.providerHealth.title
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
            "symbol", "strategyId", "direction", "status", "paperOnly", "qualificationStatus", "reasons", "sealedMetrics",
        ], context: "candidate")
        let directionValue = try requiredString(candidate, "direction", context: "candidate")
        guard let direction = ResearchRoundDirection(rawValue: directionValue) else { throw invalid("spot short or direction") }
        guard try requiredBool(candidate, "paperOnly", context: "candidate"),
              try requiredString(candidate, "qualificationStatus", context: "candidate") == "unqualified"
        else { throw invalid("candidate qualification") }
        return ResearchRoundCandidate(
            symbol: try requiredSymbol(candidate),
            strategyID: try requiredString(candidate, "strategyId", context: "candidate"),
            direction: direction,
            status: try candidateStatus(candidate, "status", context: "candidate"),
            reasons: try reasonList(candidate, "reasons", context: "candidate"),
            sealedMetrics: try sealedMetrics(candidate)
        )
    }
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
