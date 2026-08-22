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

struct BacktestSnapshot: Decodable, Identifiable, Sendable {
    let backtestId: String
    let assetClass: AssetClass
    let strategyName: String
    let readiness: BacktestReadiness
    let verdict: String
    let sampleSize: Int
    let developmentMetrics: [String: Double?]
    let finalTestMetrics: [String: Double?]
    let assumptions: [String]
    let warnings: [String]
    let equityCurve: [BacktestPoint]
    let drawdownCurve: [BacktestPoint]

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
