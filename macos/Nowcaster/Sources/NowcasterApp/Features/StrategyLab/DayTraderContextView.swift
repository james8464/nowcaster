import SwiftUI

struct DayTraderContextView: View {
    let service: LivePaperSignalService

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                Label("Decision context", systemImage: "chart.xyaxis.line").font(.headline)
                Text("How market conditions support the current research decision.")
                    .foregroundStyle(.secondary)
                TimelineView(.periodic(from: .now, by: 1)) { timeline in
                    let current = service.decisionEvidence?.currentContexts(now: timeline.date, isRunning: service.isRunning) ?? []
                    if current.isEmpty {
                        Label("Current context unavailable", systemImage: "pause.circle")
                        Text(service.decisionMessage ?? "Fresh, validated market context will appear during collection. Earlier observations are no longer current.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        ForEach(current) { context in contextCard(context) }
                    }
                }
                Divider()
                DisclosureGroup("Historical hypothetical outcomes") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Completed paper hypotheses only. These are historical observations, not current signals or executable fills.")
                            .font(.caption).foregroundStyle(.secondary)
                        if let outcomes = service.decisionEvidence?.outcomes, !outcomes.isEmpty {
                            ForEach(outcomes.reversed()) { outcome in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("\(outcome.symbol) · \(outcome.exitReason.researchTitle)").font(.subheadline.weight(.medium))
                                    LabeledContent("Observed completion", value: outcome.completedAt.formatted(date: .abbreviated, time: .standard))
                                    Text("Hypothesis began \(outcome.createdAt.formatted(date: .abbreviated, time: .standard))")
                                        .foregroundStyle(.secondary)
                                }.font(.caption).accessibilityElement(children: .combine)
                                Divider()
                            }
                        } else {
                            Text("No validated completed outcomes loaded.").foregroundStyle(.secondary)
                        }
                    }.padding(.top, 4)
                }.accessibilityIdentifier("dayTrader.historicalOutcomes")
            }.padding(4).textSelection(.enabled)
        }.accessibilityIdentifier("strategyLab.dayTraderContext")
    }

    private func contextCard(_ context: DayTraderContext) -> some View {
        GroupBox(context.symbol) {
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Market conditions", value: context.regime.researchTitle)
                LabeledContent("Context assessment", value: context.posture == "long_research" ? "Criteria met" : "Stand aside")
                LabeledContent("Trend agreement", value: context.trends.map { "\($0.minutes)m: \($0.direction.researchTitle)" }.joined(separator: " · "))
                LabeledContent("Spread", value: basisPoints(context.spreadBps))
                LabeledContent("Volatility", value: basisPoints(context.volatilityBps))
                LabeledContent("Quote balance", value: context.quoteImbalance ?? "Unavailable")
                LabeledContent("Session", value: context.session.researchTitle)
                LabeledContent("Scheduled events", value: context.calendarBlackout.map { $0 ? "Blackout — stand aside" : "Outside the configured blackout" } ?? "Calendar unavailable")
                LabeledContent("Evidence expires", value: context.expiresAt.formatted(date: .omitted, time: .standard))
                if !context.reasons.isEmpty {
                    Text(context.reasons.map(\.researchTitle).joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("A basis point is 0.01%. Trend agreement describes observed direction; it is not a probability of profit.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Current publication also depends on the collection and candidate checks shown in Live paper signals.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(4)
        }
    }

    private func basisPoints(_ value: String?) -> String {
        guard let value, let number = Double(value) else { return "Unavailable" }
        return number.formatted(.number.precision(.fractionLength(0...2))) + " basis points"
    }
}
