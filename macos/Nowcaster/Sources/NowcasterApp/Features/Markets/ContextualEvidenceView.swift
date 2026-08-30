import SwiftUI

struct ContextualEvidenceView: View {
    let evidence: any ContextualEvidenceProviding
    var title = "Asset selection"

    private var presentation: ContextualResearchPresentation { ContextualResearchPresentation(evidence: evidence) }

    var body: some View {
        GroupBox(title) {
            VStack(alignment: .leading, spacing: 12) {
                Label(presentation.eligibilityTitle, systemImage: presentation.eligibilitySymbol)
                    .font(.headline)
                if evidence.hasContextualEvidence {
                    selection
                    if !presentation.reasons.isEmpty {
                        ForEach(Array(presentation.reasons.enumerated()), id: \.offset) { _, reason in
                            Label(reason, systemImage: "info.circle").font(.callout)
                        }
                    }
                    if let date = evidence.contextualEffectiveAt {
                        Text("Assessed \(date.formatted(date: .abbreviated, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if presentation.isStale {
                        Label("Historical assessment — refresh before considering a current opportunity.", systemImage: "clock")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    Divider()
                    DisclosureGroup("Market conditions") { regimes.padding(.top, 8) }
                    DisclosureGroup("Liquidity and risk evidence") { liquidity.padding(.top, 8) }
                    DisclosureGroup("Strategy influence") { influence.padding(.top, 8) }
                    Text(presentation.sizeDisclaimer).font(.footnote).foregroundStyle(.secondary)
                } else {
                    Text("Run Assess Markets in Strategy Lab after collecting compatible history. Missing evidence never makes an asset eligible.")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
        .accessibilityIdentifier("contextual.evidence")
    }

    private var selection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(presentation.portfolioTitle).fontWeight(.medium)
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                row("Asset profile", evidence.assetProfile?.researchTitle ?? "Unavailable")
                row("Direction", evidence.contextualDirection?.researchTitle ?? "Unavailable")
                row("Research rank", evidence.portfolioRank.map(String.init) ?? "Unavailable")
                row("Exposure ceiling", ResearchFormatting.percentage(evidence.researchSizeCeiling))
            }
            ForEach(Array(presentation.conflicts.enumerated()), id: \.offset) { _, conflict in
                Label(conflict, systemImage: "minus.circle").font(.callout)
            }
        }
    }

    private var regimes: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(presentation.regimes) { regime in
                Gauge(value: regime.probability) {
                    Text(regime.title)
                } currentValueLabel: {
                    Text(regime.probability.formatted(.percent.precision(.fractionLength(0))))
                        .monospacedDigit()
                }
                .gaugeStyle(.linearCapacity)
                .accessibilityLabel(regime.title)
                .accessibilityValue(regime.probability.formatted(.percent.precision(.fractionLength(0))))
            }
            Text("These probabilities describe market conditions, not the chance of making money.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var liquidity: some View {
        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
            row("Bid–ask spread", evidence.spreadBps.map { "\(ResearchFormatting.metric($0)) basis points" } ?? "Not observed")
            row("Observed depth", ResearchFormatting.currency(evidence.depthNotional))
            row("Price impact", evidence.estimatedPriceImpactBps.map { "\(ResearchFormatting.metric($0)) basis points" } ?? "Not observed")
            row("History coverage", ResearchFormatting.percentage(evidence.marketCoverageRatio))
            row("Risk estimate", evidence.covarianceStatus?.researchTitle ?? "Unavailable")
            row("Drift check", evidence.contextualDriftStatus?.researchTitle ?? "Unavailable")
            row("Independent strategies", ResearchFormatting.metric(evidence.effectiveStrategyCount))
        }
    }

    private var influence: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(presentation.influenceTitle)
            if let weight = evidence.finalWeight {
                Text("Contextual strategy weight: \(weight.formatted(.percent.precision(.fractionLength(1))))")
            }
            if let count = evidence.effectiveObservations {
                Text("\(count.formatted(.number.precision(.fractionLength(1)))) effective observations")
            }
            Text("Sparse asset-specific history receives less influence. Broader evidence supplies a cautious starting point; correlated strategies do not count as independent votes. See Strategy Lab for each strategy’s contribution.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func row(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }
}
