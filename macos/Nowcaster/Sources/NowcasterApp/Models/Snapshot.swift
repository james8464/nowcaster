import Foundation

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
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
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
    let symbol: String
    let interval: String
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

    var id: String { "\(strategyId)-\(version)-\(symbol)-\(interval)" }
}

struct EnsembleComponentSnapshot: Decodable, Identifiable, Sendable {
    let strategyId: String
    let version: String
    let family: String
    let symbol: String
    let interval: String
    let mode: String
    let effectiveAt: Date
    let weight: Double
    let contribution: Double?
    let evidence: [String: JSONValue]

    var id: String { "\(strategyId)-\(version)-\(symbol)-\(interval)-\(mode)-\(effectiveAt.timeIntervalSince1970)" }
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
    func validateSchemaV2() throws {
        guard schemaVersion == 2 else { throw SnapshotValidationError.unsupportedSchema(schemaVersion) }
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
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
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
            let dateOnly = DateFormatter()
            dateOnly.calendar = Calendar(identifier: .iso8601)
            dateOnly.locale = Locale(identifier: "en_US_POSIX")
            dateOnly.timeZone = TimeZone(secondsFromGMT: 0)
            dateOnly.dateFormat = "yyyy-MM-dd"
            if let date = dateOnly.date(from: value) {
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
