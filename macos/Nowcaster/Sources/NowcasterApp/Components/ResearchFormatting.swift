import Foundation

enum ResearchFormatting {
    static func percentage(_ value: Double?, precision: Int = 1) -> String {
        guard let value else { return "—" }
        return value.formatted(.percent.precision(.fractionLength(precision)).sign(strategy: .always()))
    }

    static func probability(_ value: Double?) -> String {
        guard let value else { return "Not calibrated" }
        return value.formatted(.percent.precision(.fractionLength(0)))
    }

    static func probabilityRange(lower: Double?, upper: Double?) -> String {
        guard let lower, let upper else { return "Range unavailable" }
        let low = lower.formatted(.percent.precision(.fractionLength(0)))
        let high = upper.formatted(.percent.precision(.fractionLength(0)))
        return "\(low)–\(high)"
    }

    static func duration(seconds: Double?) -> String {
        guard let seconds else { return "—" }
        if seconds < 60 { return "\(Int(seconds.rounded())) sec" }
        if seconds < 3_600 { return "\(Int((seconds / 60).rounded())) min" }
        return (seconds / 3_600).formatted(.number.precision(.fractionLength(1))) + " hr"
    }

    static func milliseconds(_ value: Double?) -> String {
        guard let value else { return "—" }
        return value.formatted(.number.precision(.fractionLength(0))) + " ms"
    }

    static func evidenceAccessibilitySummary(_ signal: ResearchSignalSnapshot) -> String {
        var parts: [String] = []
        if let probability = signal.calibratedProbability {
            parts.append("calibrated probability \(Int((probability * 100).rounded())) percent")
        } else {
            parts.append("calibrated probability unavailable")
        }
        if let lower = signal.probabilityLowerBound, let upper = signal.probabilityUpperBound {
            parts.append("range \(Int((lower * 100).rounded())) to \(Int((upper * 100).rounded())) percent")
        }
        if let edge = signal.lowerNetEdge {
            parts.append("lower cost-adjusted edge \(percentage(edge, precision: 2))")
        }
        if let drift = signal.driftStatus {
            parts.append("drift \(drift.replacingOccurrences(of: "_", with: " "))")
        }
        return parts.joined(separator: ", ")
    }

    static func compactEvidence(_ signal: ResearchSignalSnapshot) -> String? {
        var parts: [String] = []
        if signal.probabilityLowerBound != nil, signal.probabilityUpperBound != nil {
            parts.append("range \(probabilityRange(lower: signal.probabilityLowerBound, upper: signal.probabilityUpperBound))")
        }
        if let edge = signal.lowerNetEdge {
            parts.append("lower edge \(percentage(edge, precision: 2))")
        }
        if let drift = signal.driftStatus {
            parts.append("drift \(drift.replacingOccurrences(of: "_", with: " "))")
        }
        return parts.joined(separator: " · ").isEmpty ? nil : parts.joined(separator: " · ")
    }

    static func currency(_ value: Double?) -> String {
        guard let value else { return "—" }
        return value.formatted(.currency(code: "USD").precision(.fractionLength(value >= 1_000 ? 0 : 2)))
    }

    static func compactNumber(_ value: Double?) -> String {
        guard let value else { return "—" }
        return value.formatted(.number.notation(.compactName).precision(.fractionLength(1)))
    }

    static func metric(_ value: Double?) -> String {
        guard let value else { return "—" }
        return value.formatted(.number.precision(.fractionLength(2)))
    }
}
