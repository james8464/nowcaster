import Foundation

struct ChartAccessibility: Identifiable, Sendable {
    let id: String
    let summary: String
    let rows: [AccessibleChartRow]

    static let fixtureCharts = [
        ChartAccessibility(
            id: "price-history",
            summary: "Adjusted daily close over the selected period, with first-to-last change described in text.",
            rows: [AccessibleChartRow(label: "Example date", value: "$100.00")]
        ),
        ChartAccessibility(
            id: "revenue-comparison",
            summary: "Model forecast, expectation source, and reported actual are compared as labelled bars.",
            rows: [AccessibleChartRow(label: "Model forecast", value: "$1.0B")]
        ),
        ChartAccessibility(
            id: "backtest-equity",
            summary: "Net equity, final-test boundary, drawdown, rolling risk, and exposure all have table alternatives.",
            rows: [AccessibleChartRow(label: "Final test begins", value: "Explicitly marked")]
        ),
        ChartAccessibility(
            id: "model-error",
            summary: "Out-of-sample error is compared by model, feature ablation, and forecast horizon.",
            rows: [AccessibleChartRow(label: "MAE", value: "Lower is better")]
        ),
    ]
}

extension ResearchPosture {
    var accessibilityDescription: String {
        switch self {
        case .longResearch:
            "Long research posture. Upward directional evidence; further investigation required."
        case .shortResearch:
            "Short research posture. Downward directional evidence; further investigation required."
        case .abstain:
            "Abstain. The declared evidence gate was not cleared."
        case let .unknown(value):
            "Unknown research posture: \(value)."
        }
    }
}
