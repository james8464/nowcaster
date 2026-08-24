import Foundation

enum SnapshotDecodingLimits {
    // The bundled fixture is about 2.5 MB; 8 MiB leaves practical headroom while
    // rejecting an unbounded native-app document before JSON decoding begins.
    static let maximumSnapshotBytes = 8 * 1_024 * 1_024
    static let maximumEvidenceDepth = 16
    static let maximumEvidenceNodes = 50_000
    static let maximumCollectionLength = 2_000
    static let maximumStringBytes = 16 * 1_024
}

private final class SnapshotDecodingBudget: @unchecked Sendable {
    private var nodes = 0
    private let lock = NSLock()

    func consumeNode(codingPath: [any CodingKey]) throws {
        let count = lock.withLock {
            nodes += 1
            return nodes
        }
        guard count <= SnapshotDecodingLimits.maximumEvidenceNodes else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: codingPath, debugDescription: "Research evidence exceeds the node limit")
            )
        }
    }
}

private extension CodingUserInfoKey {
    static let snapshotDecodingBudget = CodingUserInfoKey(rawValue: "Nowcaster.snapshotDecodingBudget")!
}

private struct DynamicJSONKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}

enum AssetClass: Hashable, Sendable {
    case equity
    case crypto
    case unknown(String)
}

extension AssetClass: Codable {
    init(from decoder: Decoder) throws {
        let rawValue = try decoder.singleValueContainer().decode(String.self)
        self = switch rawValue {
        case "equity": .equity
        case "crypto": .crypto
        default: .unknown(rawValue)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        let rawValue = switch self {
        case .equity: "equity"
        case .crypto: "crypto"
        case let .unknown(value): value
        }
        try container.encode(rawValue)
    }
}

enum ResearchPosture: Hashable, Sendable {
    case longResearch
    case shortResearch
    case abstain
    case unknown(String)
}

extension ResearchPosture: Codable {
    init(from decoder: Decoder) throws {
        let rawValue = try decoder.singleValueContainer().decode(String.self)
        self = switch rawValue {
        case "long_research": .longResearch
        case "short_research": .shortResearch
        case "abstain": .abstain
        default: .unknown(rawValue)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        let rawValue = switch self {
        case .longResearch: "long_research"
        case .shortResearch: "short_research"
        case .abstain: "abstain"
        case let .unknown(value): value
        }
        try container.encode(rawValue)
    }
}

enum BacktestReadiness: Hashable, Sendable {
    case decisionReady
    case researchOnly
    case notReady
    case unknown(String)
}

extension BacktestReadiness: Codable {
    init(from decoder: Decoder) throws {
        let rawValue = try decoder.singleValueContainer().decode(String.self)
        self = switch rawValue {
        case "decision_ready": .decisionReady
        case "research_only": .researchOnly
        case "not_ready": .notReady
        default: .unknown(rawValue)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        let rawValue = switch self {
        case .decisionReady: "decision_ready"
        case .researchOnly: "research_only"
        case .notReady: "not_ready"
        case let .unknown(value): value
        }
        try container.encode(rawValue)
    }
}

struct NowcasterSnapshot: Decodable, Sendable {
    let schemaVersion: Int
    let metadata: SnapshotMetadata
    let overview: OverviewSnapshot
    let instruments: [InstrumentSnapshot]
    let earnings: [EarningsSnapshot]
    let signals: [ResearchSignalSnapshot]
    let modelDiagnostics: [ModelDiagnosticSnapshot]
    let backtests: [BacktestSnapshot]
    let qualityIssues: [QualityIssueSnapshot]
    let pipelineRuns: [PipelineRunSnapshot]
    let strategies: [StrategySnapshot]
    let ensembleComponents: [EnsembleComponentSnapshot]
    let datasetCoverage: [DatasetCoverageSnapshot]
    let learningRuns: [LearningRunSnapshot]
    let causalAudits: [CausalAuditSnapshot]
    let brokerStatus: BrokerStatusSnapshot?
    let brokerPositions: [BrokerPositionSnapshot]?
    let brokerOrders: [BrokerOrderSnapshot]?
    let brokerEvents: [BrokerEventSnapshot]?
    let riskStatus: RiskStatusSnapshot?
    let forwardReadiness: ForwardReadinessSnapshot?
    let emergencyStatus: EmergencyStatusSnapshot?
}

struct BrokerStatusSnapshot: Decodable, Sendable {
    let environment: String
    let state: String
    let accountSuffix: String?
    let sessionStatus: String
    let reconciledAt: Date?
    let unresolvedMismatches: Int
}

struct BrokerPositionSnapshot: Decodable, Identifiable, Sendable {
    let symbol: String
    let quantity: Double
    let marketValue: Double
    let unrealizedPnl: Double
    let receivedAt: Date
    var id: String { symbol }
}

struct BrokerOrderSnapshot: Decodable, Identifiable, Sendable {
    let clientOrderId: String
    let symbol: String
    let side: String
    let quantity: Double
    let filledQuantity: Double
    let limitPrice: Double
    let status: String
    let updatedAt: Date
    var id: String { clientOrderId }
}

struct BrokerEventSnapshot: Decodable, Identifiable, Sendable {
    let eventId: String
    let clientOrderId: String
    let event: String
    let knownEvent: Bool
    let status: String
    let receivedAt: Date
    var id: String { eventId }
}

struct RiskStatusSnapshot: Decodable, Sendable {
    let state: String
    let allowed: Bool
    let reasons: [String]
    let utilization: [String: StringOrInt]
    let decidedAt: Date?
}

enum StringOrInt: Decodable, Sendable {
    case string(String)
    case integer(Int)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) { self = .integer(value) }
        else { self = .string(try container.decode(String.self)) }
    }
}

struct ReadinessGateSnapshot: Decodable, Identifiable, Sendable {
    let name: String
    let passed: Bool
    let detail: String
    var id: String { name }
}

struct ForwardReadinessSnapshot: Decodable, Sendable {
    let state: String
    let cohortHash: String?
    let observedPeriods: Int
    let closedTrades: Int
    let receiptExpiresAt: Date?
    let gates: [ReadinessGateSnapshot]
}

struct EmergencyStatusSnapshot: Decodable, Sendable {
    let frozen: Bool
    let flattenState: String
    let reason: String?
    let observedAt: Date?
}

struct SnapshotMetadata: Decodable, Sendable {
    let generatedAt: Date
    let gitCommit: String
    let dataMode: String
    let sourcePosture: String
    let expectationMode: String
    let lastRefresh: Date?
}

struct OverviewSnapshot: Decodable, Sendable {
    let companyCount: Int
    let instrumentCount: Int
    let companyQuarterCount: Int
    let alternativeObservationCount: Int
    let forecastCount: Int
    let signalCount: Int
    let eventWindowCount: Int
    let qualityIssueCount: Int
    let forecastMaeImprovement: Double?
    let alternativeIncrementalMaeImprovement: Double?
    let eventSpread: Double?
}

struct PricePoint: Decodable, Identifiable, Sendable {
    let date: Date
    let close: Double
    let volume: Double?

    var id: Date { date }
}

struct InstrumentSnapshot: Decodable, Identifiable, Sendable {
    let instrumentId: String
    let symbol: String
    let displayName: String
    let assetClass: AssetClass
    let lastPrice: Double?
    let dailyReturn: Double?
    let weeklyReturn: Double?
    let realizedVolatility: Double?
    let trendRegime: String
    let freshnessDate: Date?
    let priceHistory: [PricePoint]

    var id: String { instrumentId }
}

struct EarningsSnapshot: Decodable, Identifiable, Sendable {
    let forecastId: String
    let companyId: String
    let fiscalQuarter: String
    let earningsDate: Date
    let forecastCutoffDate: Date
    let horizonDays: Int
    let modelName: String
    let ablation: String
    let forecastRevenue: Double
    let actualRevenue: Double?
    let expectationRevenue: Double
    let expectationMode: String
    let variant: Double
    let variantZscore: Double?
    let confidenceScore: Double?

    var id: String { forecastId }
}

struct ResearchSignalSnapshot: Decodable, Identifiable, Sendable {
    let signalId: String
    let instrumentId: String
    let assetClass: AssetClass
    let decisionDate: Date
    let horizon: String
    let posture: ResearchPosture
    let eligibility: String
    let strength: Double?
    let calibratedProbability: Double?
    let confidenceScore: Double?
    let catalyst: String
    let invalidation: String
    let evidenceSummary: String
    let reasons: [String]

    var id: String { signalId }
}

struct ModelDiagnosticSnapshot: Decodable, Identifiable, Sendable {
    let modelName: String
    let ablation: String
    let horizonDays: Int
    let observations: Int
    let mae: Double
    let rmse: Double
    let mape: Double?
    let directionalAccuracy: Double?

    var id: String { "\(modelName)-\(ablation)-\(horizonDays)" }
}

struct BacktestPoint: Decodable, Identifiable, Sendable {
    let date: Date
    let value: Double

    var id: Date { date }
}

struct SensitivitySnapshot: Decodable, Identifiable, Sendable {
    let scenario: String
    let costMultiplier: Double
    let metrics: [String: Double?]

    var id: String { scenario }
}

struct BacktestSnapshot: Decodable, Identifiable, Sendable {
    let backtestId: String
    let assetClass: AssetClass
    let strategyName: String
    let readiness: BacktestReadiness
    let verdict: String
    let sampleSize: Int
    let developmentMetrics: [String: Double?]
    let finalTestMetrics: [String: Double?]
    let fullMetrics: [String: Double?]
    let robustness: [String: Double?]
    let assumptions: [String]
    let warnings: [String]
    let equityCurve: [BacktestPoint]
    let drawdownCurve: [BacktestPoint]
    let rollingSharpeCurve: [BacktestPoint]
    let exposureCurve: [BacktestPoint]
    let turnoverCurve: [BacktestPoint]
    let monthlyReturns: [BacktestPoint]
    let sensitivities: [SensitivitySnapshot]

    var id: String { backtestId }
}

struct QualityIssueSnapshot: Decodable, Identifiable, Sendable {
    let issueId: String
    let stage: String
    let severity: String
    let rule: String
    let entityKey: String
    let message: String
    let detectedAt: Date

    var id: String { issueId }
}

struct PipelineRunSnapshot: Decodable, Identifiable, Sendable {
    let pipelineRunId: String
    let command: String
    let mode: String
    let startedAt: Date
    let endedAt: Date?
    let status: String
    let rowCounts: [String: Int]
    let errorSummary: String?

    var id: String { pipelineRunId }
}

indirect enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        try (decoder.userInfo[.snapshotDecodingBudget] as? SnapshotDecodingBudget)?.consumeNode(
            codingPath: decoder.codingPath
        )
        let evidenceRoot = decoder.codingPath.lastIndex {
            ["evidence", "details"].contains($0.stringValue)
        }
        let depth = evidenceRoot.map { decoder.codingPath.distance(from: $0, to: decoder.codingPath.endIndex) - 1 }
            ?? decoder.codingPath.count
        guard depth <= SnapshotDecodingLimits.maximumEvidenceDepth else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath, debugDescription: "Research evidence exceeds the depth limit")
            )
        }
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            guard value.utf8.count <= SnapshotDecodingLimits.maximumStringBytes else {
                throw DecodingError.dataCorruptedError(
                    in: container,
                    debugDescription: "Research evidence string exceeds the byte limit"
                )
            }
            self = .string(value)
        } else {
            if let keyed = try? decoder.container(keyedBy: DynamicJSONKey.self) {
                guard keyed.allKeys.count <= SnapshotDecodingLimits.maximumCollectionLength else {
                    throw DecodingError.dataCorrupted(
                        .init(codingPath: decoder.codingPath, debugDescription: "Research evidence collection is too large")
                    )
                }
                var value: [String: JSONValue] = [:]
                value.reserveCapacity(keyed.allKeys.count)
                for key in keyed.allKeys {
                    guard key.stringValue.utf8.count <= SnapshotDecodingLimits.maximumStringBytes else {
                        throw DecodingError.dataCorrupted(
                            .init(codingPath: decoder.codingPath, debugDescription: "Research evidence key is too large")
                        )
                    }
                    value[key.stringValue] = try keyed.decode(JSONValue.self, forKey: key)
                }
                self = .object(value)
                return
            }
            var unkeyed = try decoder.unkeyedContainer()
            if let count = unkeyed.count, count > SnapshotDecodingLimits.maximumCollectionLength {
                throw DecodingError.dataCorrupted(
                    .init(codingPath: decoder.codingPath, debugDescription: "Research evidence collection is too large")
                )
            }
            var value: [JSONValue] = []
            while !unkeyed.isAtEnd {
                guard value.count < SnapshotDecodingLimits.maximumCollectionLength else {
                    throw DecodingError.dataCorrupted(
                        .init(codingPath: decoder.codingPath, debugDescription: "Research evidence collection is too large")
                    )
                }
                value.append(try unkeyed.decode(JSONValue.self))
            }
            self = .array(value)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value): try container.encode(value)
        case let .number(value): try container.encode(value)
        case let .bool(value): try container.encode(value)
        case let .object(value): try container.encode(value)
        case let .array(value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

enum NoRepaintBadge: String, Codable, Sendable {
    case passed
    case failed
    case notAudited = "not_audited"
}

struct StrategySnapshot: Decodable, Identifiable, Sendable {
    let strategyId: String
    let version: String
    let family: String
    let datasetHash: String
    let symbol: String
    let interval: String
    let mode: String
    let cohortId: String?
    let state: String
    let weight: Double
    let developmentMetrics: [String: Double?]
    let finalTestMetrics: [String: Double?]
    let warnings: [String]
    let generation: Int
    let progress: Double
    let complexity: Int?
    let promotionState: String
    let causalAuditPassed: Bool?
    let noRepaintBadge: NoRepaintBadge
    let latestRunAt: Date?

    var id: String {
        [strategyId, version, datasetHash, symbol, interval, mode, cohortId ?? "legacy"].joined(separator: "-")
    }
}

struct EnsembleComponentSnapshot: Decodable, Identifiable, Sendable {
    let strategyId: String
    let version: String
    let family: String
    let datasetHash: String
    let symbol: String
    let interval: String
    let mode: String
    let cohortId: String?
    let effectiveAt: Date
    let weight: Double
    let contribution: Double?
    let evidence: [String: JSONValue]

    var id: String {
        [strategyId, version, datasetHash, symbol, interval, mode, cohortId ?? "legacy"]
            .joined(separator: "-") + "-\(effectiveAt.timeIntervalSince1970)"
    }
}

struct DatasetGapSnapshot: Decodable, Identifiable, Sendable {
    let start: Date
    let end: Date
    let missingBars: Int

    var id: String { "\(start.timeIntervalSince1970)-\(end.timeIntervalSince1970)" }
}

struct DatasetCoverageSnapshot: Decodable, Identifiable, Sendable {
    let datasetHash: String
    let provider: String
    let feed: String
    let symbol: String
    let interval: String
    let requestedStart: Date
    let requestedEnd: Date
    let coverageStart: Date?
    let coverageEnd: Date?
    let rowCount: Int
    let gaps: [DatasetGapSnapshot]
    let complete: Bool
    let calendarId: String
    let calendarVersion: String

    var id: String { "\(datasetHash)-\(provider)-\(feed)-\(symbol)-\(interval)" }
}

struct LearningTrialSnapshot: Decodable, Identifiable, Sendable {
    let trialId: String
    let candidateHash: String
    let status: String
    let fitness: Double?
    let evaluatedAt: Date
    let ruleText: String
    let complexity: Int
    let errorSummary: String?

    var id: String { trialId }
}

struct DiscoveredRuleSnapshot: Decodable, Identifiable, Sendable {
    let ruleId: String
    let strategyId: String
    let version: String
    let state: String
    let ruleText: String
    let fitness: Double?
    let complexity: Int
    let discoveredAt: Date
    let evidenceThrough: Date?
    let promotionState: String
    let causalAuditId: String?
    let noRepaintBadge: NoRepaintBadge

    var id: String { ruleId }
}

struct LearningRunSnapshot: Decodable, Identifiable, Sendable {
    let learningRunId: String
    let state: String
    let evaluatedCandidates: Int
    let evaluationBudget: Int
    let bestRule: String?
    let bestRuleDetail: DiscoveredRuleSnapshot?
    let finalBoundary: Date
    let generation: Int
    let progress: Double
    let trials: [LearningTrialSnapshot]
    let discoveredRules: [DiscoveredRuleSnapshot]
    let promotionState: String
    let causalAuditId: String?
    let noRepaintBadge: NoRepaintBadge

    var id: String { learningRunId }
}

struct CausalAuditSnapshot: Decodable, Identifiable, Sendable {
    let auditId: String
    let datasetHash: String
    let strategyId: String
    let version: String
    let symbol: String
    let interval: String
    let mode: String
    let auditedAt: Date
    let passed: Bool
    let outerBlockConsumed: Bool
    let details: [String: JSONValue]
    let noRepaintBadge: NoRepaintBadge

    var id: String { auditId }
}

extension NowcasterSnapshot {
    func validateSchemaV3() throws {
        guard schemaVersion == 3 else { throw SnapshotValidationError.unsupportedSchema(schemaVersion) }
        guard (brokerPositions?.count ?? 0) <= 100,
              (brokerOrders?.count ?? 0) <= 100,
              (brokerEvents?.count ?? 0) <= 200,
              (forwardReadiness?.gates.count ?? 0) <= 50,
              (brokerStatus?.unresolvedMismatches ?? 0) >= 0,
              (forwardReadiness?.observedPeriods ?? 0) >= 0,
              (forwardReadiness?.closedTrades ?? 0) >= 0
        else { throw SnapshotValidationError.invalidResearchEvidence("execution bounds") }
        guard strategies.allSatisfy({
            $0.weight >= 0 && (0 ... 1).contains($0.progress) && $0.generation >= 1 && ($0.complexity ?? 0) >= 0
        }) else { throw SnapshotValidationError.invalidResearchEvidence("strategy bounds") }
        guard ensembleComponents.allSatisfy({ $0.weight >= 0 }) else {
            throw SnapshotValidationError.invalidResearchEvidence("ensemble weight")
        }
        guard datasetCoverage.allSatisfy({ coverage in
            coverage.rowCount >= 0 && coverage.requestedStart < coverage.requestedEnd
                && coverage.gaps.allSatisfy { $0.missingBars > 0 && $0.start < $0.end }
        }) else { throw SnapshotValidationError.invalidResearchEvidence("dataset coverage") }
        guard learningRuns.allSatisfy({ run in
            run.evaluationBudget > 0 && run.evaluatedCandidates >= 0
                && run.evaluatedCandidates <= run.evaluationBudget && run.generation >= 1
                && (0 ... 1).contains(run.progress) && run.trials.allSatisfy { $0.complexity >= 0 }
                && run.discoveredRules.allSatisfy { $0.complexity >= 0 }
        }) else { throw SnapshotValidationError.invalidResearchEvidence("learning run bounds") }
        var evidenceNodes = 0
        for component in ensembleComponents {
            try validateEvidence(component.evidence, nodes: &evidenceNodes, depth: 0)
        }
        for audit in causalAudits {
            try validateEvidence(audit.details, nodes: &evidenceNodes, depth: 0)
        }
    }

    private func validateEvidence(
        _ value: [String: JSONValue],
        nodes: inout Int,
        depth: Int
    ) throws {
        guard value.count <= SnapshotDecodingLimits.maximumCollectionLength else {
            throw SnapshotValidationError.invalidResearchEvidence("collection length")
        }
        for (key, nested) in value {
            guard key.utf8.count <= SnapshotDecodingLimits.maximumStringBytes else {
                throw SnapshotValidationError.invalidResearchEvidence("key length")
            }
            try validateEvidence(nested, nodes: &nodes, depth: depth + 1)
        }
    }

    private func validateEvidence(_ value: JSONValue, nodes: inout Int, depth: Int) throws {
        nodes += 1
        guard nodes <= SnapshotDecodingLimits.maximumEvidenceNodes,
              depth <= SnapshotDecodingLimits.maximumEvidenceDepth
        else { throw SnapshotValidationError.invalidResearchEvidence("evidence complexity") }
        switch value {
        case let .string(string):
            guard string.utf8.count <= SnapshotDecodingLimits.maximumStringBytes else {
                throw SnapshotValidationError.invalidResearchEvidence("string length")
            }
        case let .object(object):
            try validateEvidence(object, nodes: &nodes, depth: depth)
        case let .array(array):
            guard array.count <= SnapshotDecodingLimits.maximumCollectionLength else {
                throw SnapshotValidationError.invalidResearchEvidence("collection length")
            }
            for nested in array {
                try validateEvidence(nested, nodes: &nodes, depth: depth + 1)
            }
        case .number, .bool, .null:
            break
        }
    }
}

enum SnapshotValidationError: Error {
    case unsupportedSchema(Int)
    case invalidResearchEvidence(String)
}

extension JSONDecoder {
    static var nowcaster: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.userInfo[.snapshotDecodingBudget] = SnapshotDecodingBudget()
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            let key = decoder.codingPath.last?.stringValue ?? ""
            let legacyDateOnlyKeys: Set<String> = [
                "date", "earnings_date", "earningsDate", "forecast_cutoff_date", "forecastCutoffDate",
                "decision_date", "decisionDate", "freshness_date", "freshnessDate",
            ]
            if legacyDateOnlyKeys.contains(key), value.count == 10, !value.contains("T") {
                let dateOnly = DateFormatter()
                dateOnly.calendar = Calendar(identifier: .iso8601)
                dateOnly.locale = Locale(identifier: "en_US_POSIX")
                dateOnly.timeZone = TimeZone(secondsFromGMT: 0)
                dateOnly.dateFormat = "yyyy-MM-dd"
                if let date = dateOnly.date(from: value) { return date }
            }
            guard value.contains("T"), value.hasSuffix("Z") else {
                throw DecodingError.dataCorruptedError(
                    in: try decoder.singleValueContainer(),
                    debugDescription: "Instant must be an ISO-8601 timestamp with literal Z UTC: \(value)"
                )
            }
            let fractional = ISO8601DateFormatter()
            fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = fractional.date(from: value) {
                return date
            }
            let internet = ISO8601DateFormatter()
            internet.formatOptions = [.withInternetDateTime]
            if let date = internet.date(from: value) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: try decoder.singleValueContainer(),
                debugDescription: "Unsupported Nowcaster date: \(value)"
            )
        }
        return decoder
    }
}
