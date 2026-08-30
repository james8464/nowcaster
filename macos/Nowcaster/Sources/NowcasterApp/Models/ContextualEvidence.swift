import Foundation

enum AssetEligibilityState: Hashable, Sendable, Decodable {
    case eligible
    case watch
    case blocked
    case unknown(String)

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = switch value {
        case "eligible": .eligible
        case "watch": .watch
        case "blocked": .blocked
        default: .unknown(value)
        }
    }
}

/// Flat, optional schema-v5 fields shared by assets, signals and strategy evidence.
protocol ContextualEvidenceProviding: Sendable {
    var assetProfile: String? { get }
    var eligibilityState: AssetEligibilityState? { get }
    var eligibilityReasons: [String]? { get }
    var eligibilityQuality: Double? { get }
    var eligibilityHash: String? { get }
    var contextHash: String? { get }
    var contextualDatasetHash: String? { get }
    var contextualPolicyHash: String? { get }
    var contextualEffectiveAt: Date? { get }
    var contextualDirection: String? { get }
    var contextualProvider: String? { get }
    var contextualFeed: String? { get }
    var contextualInterval: String? { get }
    var contextualMode: String? { get }
    var spreadBps: Double? { get }
    var depthNotional: Double? { get }
    var estimatedPriceImpactBps: Double? { get }
    var liquidityCapacityWeight: Double? { get }
    var marketCoverageRatio: Double? { get }
    var regimeProbabilities: [String: Double]? { get }
    var posteriorUncertainty: Double? { get }
    var localWeight: Double? { get }
    var parentWeight: Double? { get }
    var finalWeight: Double? { get }
    var effectiveObservations: Double? { get }
    var effectiveStrategyCount: Double? { get }
    var covarianceStatus: String? { get }
    var portfolioRank: Int? { get }
    var portfolioSelected: Bool? { get }
    var portfolioSelectionId: String? { get }
    var portfolioDecisionHash: String? { get }
    var researchSizeCeiling: Double? { get }
    var portfolioConflicts: [String]? { get }
    var contextualDriftStatus: String? { get }
    var contextualEvidenceHash: String? { get }
}

extension ContextualEvidenceProviding {
    var hasContextualEvidence: Bool {
        contextHash != nil && contextualEvidenceHash != nil && contextualEffectiveAt != nil
    }

    func validateContextualEvidence() throws {
        let fractions = [eligibilityQuality, liquidityCapacityWeight, marketCoverageRatio, posteriorUncertainty,
                         localWeight, parentWeight, finalWeight, researchSizeCeiling].compactMap { $0 }
        let nonnegative = [spreadBps, depthNotional, estimatedPriceImpactBps, effectiveObservations,
                           effectiveStrategyCount].compactMap { $0 }
        guard fractions.allSatisfy({ $0.isFinite && (0 ... 1).contains($0) }),
              nonnegative.allSatisfy({ $0.isFinite && $0 >= 0 }),
              portfolioRank == nil || portfolioRank! >= 1
        else { throw SnapshotValidationError.invalidResearchEvidence("contextual numeric bounds") }
        if let localWeight, let parentWeight, abs(localWeight + parentWeight - 1) > 1e-8 {
            throw SnapshotValidationError.invalidResearchEvidence("contextual influence normalization")
        }
        for reasons in [eligibilityReasons ?? [], portfolioConflicts ?? []] {
            guard reasons.count <= 64, reasons.allSatisfy({ $0.utf8.count <= 1_000 }) else {
                throw SnapshotValidationError.invalidResearchEvidence("contextual reason bounds")
            }
        }
        let hashes = [eligibilityHash, contextHash, contextualDatasetHash, contextualPolicyHash,
                      portfolioSelectionId, portfolioDecisionHash, contextualEvidenceHash].compactMap { $0 }
        let hexadecimal = Set("0123456789abcdef")
        guard hashes.allSatisfy({ $0.count == 64 && $0.allSatisfy { hexadecimal.contains($0) } }) else {
            throw SnapshotValidationError.invalidResearchEvidence("contextual evidence hash")
        }
        if let probabilities = regimeProbabilities {
            guard Set(probabilities.keys) == Set(ContextualRegimePresentation.identities),
                  probabilities.values.allSatisfy({ $0.isFinite && (0 ... 1).contains($0) }),
                  abs(probabilities.values.reduce(0, +) - 1) <= 1e-8
            else { throw SnapshotValidationError.invalidResearchEvidence("contextual regime probabilities") }
        }
        guard portfolioSelected != true || eligibilityState == .eligible else {
            throw SnapshotValidationError.invalidResearchEvidence("contextual selected asset is ineligible")
        }
        let strings = [assetProfile, contextualDirection, contextualProvider, contextualFeed, contextualInterval,
                       contextualMode, covarianceStatus, contextualDriftStatus].compactMap { $0 }
        guard strings.allSatisfy({ $0.utf8.count <= 1_000 }) else {
            throw SnapshotValidationError.invalidResearchEvidence("contextual string bounds")
        }
    }
}

struct ContextualRegimePresentation: Identifiable, Sendable {
    static let identities = ["trend_normal", "trend_elevated_volatility", "range_liquid", "stressed_or_illiquid"]
    let id: String
    let probability: Double
    var title: String {
        switch id {
        case "trend_normal": "Normal trend"
        case "trend_elevated_volatility": "Volatile trend"
        case "range_liquid": "Liquid range"
        case "stressed_or_illiquid": "Stressed or illiquid"
        default: "Unknown"
        }
    }
}

struct ContextualResearchPresentation: Sendable {
    let evidence: any ContextualEvidenceProviding
    var now: Date = Date()

    var isStale: Bool {
        guard let effectiveAt = evidence.contextualEffectiveAt else { return true }
        let age = now.timeIntervalSince(effectiveAt)
        return age < 0 || age > 24 * 60 * 60
    }
    var eligibilityTitle: String {
        guard evidence.hasContextualEvidence else { return "Not assessed" }
        guard !isStale else { return "Out of date" }
        return switch evidence.eligibilityState {
        case .eligible: "Eligible for research"
        case .watch: "Watch only"
        case .blocked: "Blocked"
        case .unknown, .none: "Not assessed"
        }
    }
    var eligibilitySymbol: String {
        guard evidence.hasContextualEvidence, !isStale else { return "clock.badge.questionmark" }
        return switch evidence.eligibilityState {
        case .eligible: "checkmark.shield"
        case .watch: "eye"
        case .blocked: "hand.raised"
        case .unknown, .none: "questionmark.circle"
        }
    }
    var regimes: [ContextualRegimePresentation] {
        guard evidence.hasContextualEvidence else { return [] }
        let probabilities = evidence.regimeProbabilities ?? [:]
        return ContextualRegimePresentation.identities.compactMap { identity in
            probabilities[identity].map { ContextualRegimePresentation(id: identity, probability: $0) }
        }
    }
    var regimeTitle: String {
        regimes.max { $0.probability < $1.probability }?.title ?? "Not assessed"
    }
    var portfolioTitle: String {
        guard evidence.hasContextualEvidence, let selected = evidence.portfolioSelected else {
            return "No authenticated selection"
        }
        if isStale { return selected ? "Previously selected" : "Previously excluded" }
        return selected ? "Selected for research" : "Not selected"
    }
    var reasons: [String] { (evidence.eligibilityReasons ?? []).map(Self.reason) }
    var conflicts: [String] { (evidence.portfolioConflicts ?? []).map(Self.reason) }
    var influenceTitle: String {
        guard let local = evidence.localWeight, let parent = evidence.parentWeight else {
            return "Strategy-specific influence unavailable"
        }
        return "\(local.formatted(.percent.precision(.fractionLength(0)))) asset evidence · \(parent.formatted(.percent.precision(.fractionLength(0)))) broader evidence"
    }
    var sizeDisclaimer: String {
        "This is a research exposure ceiling, not an order, a stop-loss limit, or a safe amount to risk. Live readiness and actual trading costs must be checked separately."
    }
    static func reason(_ code: String) -> String {
        switch code {
        case "spread_limit": "The bid–ask spread is too wide."
        case "direction_not_supported": "This product does not support this direction."
        case "depth_missing", "depth_unavailable": "Observed order-book depth is unavailable."
        case "spread_missing", "spread_unavailable": "An observed bid–ask spread is unavailable."
        case "correlation_cap", "correlation_conflict": "Another opportunity is too closely correlated."
        case "covariance_unavailable": "There is not enough independent risk evidence."
        case "nonpositive_lower_edge": "The conservative estimate of net edge is not positive."
        default: code.researchTitle
        }
    }
}

