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
