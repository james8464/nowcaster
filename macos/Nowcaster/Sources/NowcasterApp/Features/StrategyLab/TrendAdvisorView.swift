import SwiftUI

struct TrendAdvisorView: View {
    let suggestions: [TrendAdvisorSuggestion]

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                Label("Trend Advisor", systemImage: "chart.line.uptrend.xyaxis").font(.headline)
                Text("Paper-only trend research — not a trade instruction.")
                    .font(.caption).foregroundStyle(.secondary)
                if suggestions.isEmpty {
                    ContentUnavailableView("Stand aside", systemImage: "pause.circle",
                        description: Text("No causal trend evidence is included in the loaded research report."))
                } else {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        VStack(alignment: .leading, spacing: 16) {
                            ForEach(suggestions) { suggestion in
                                let display = TrendAdvisorPresentation(suggestion: suggestion, now: context.date)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text("\(suggestion.symbol) · \(display.postureTitle)").font(.headline)
                                    LabeledContent("Timeframes", value: suggestion.timeframe)
                                    Text(display.reasons).font(.caption).foregroundStyle(.secondary)
                                    if display.showsLevels {
                                        LabeledContent("Research entry zone", value: "\(suggestion.entryLow ?? "—") – \(suggestion.entryHigh ?? "—")")
                                        LabeledContent("Invalidation", value: suggestion.invalidation ?? "—")
                                        LabeledContent("Research target", value: suggestion.target ?? "—")
                                        Text("Research close conditions: invalidation or target reached, trend alignment lost, or evidence expired.")
                                            .font(.caption)
                                    }
                                    LabeledContent("Decision", value: suggestion.decisionAt.formatted(date: .abbreviated, time: .standard))
                                    LabeledContent("Expires", value: suggestion.expiresAt.formatted(date: .abbreviated, time: .standard))
                                    Text("Binance spot · \(suggestion.strategyID) · Evidence \(suggestion.evidenceHash.prefix(12))…")
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
        }
        .accessibilityIdentifier("strategyLab.trendAdvisor")
    }
}
