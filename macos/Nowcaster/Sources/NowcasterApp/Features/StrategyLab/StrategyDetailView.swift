import Charts
import SwiftUI

private struct StrategyMetricPoint: Identifiable {
    let period: String
    let metric: String
    let value: Double

    var id: String { "\(period)-\(metric)" }
}

struct StrategyDetailView: View {
    let presentation: StrategyPresentation

    private var chartPoints: [StrategyMetricPoint] {
        [
            metricPoint("Development", "Sharpe", "sharpe", presentation.strategy.developmentMetrics),
            metricPoint("Sealed final", "Sharpe", "sharpe", presentation.strategy.finalTestMetrics),
            metricPoint("Development", "Max drawdown", "maximum_drawdown", presentation.strategy.developmentMetrics),
            metricPoint("Sealed final", "Max drawdown", "maximum_drawdown", presentation.strategy.finalTestMetrics),
        ].compactMap { $0 }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(presentation.strategy.strategyId.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.title2.weight(.semibold))
                    Text("\(presentation.familyTitle) · \(presentation.strategy.symbol) · \(presentation.strategy.interval)")
                        .foregroundStyle(.secondary)
                    ResearchStatusLabel(
                        title: presentation.postureTitle,
                        systemImage: presentation.posture.symbolName,
                        color: presentation.posture.color
                    )
                    .accessibilityLabel(presentation.directionAccessibilityLabel)
                }

                Grid(alignment: .leading, horizontalSpacing: 22, verticalSpacing: 12) {
                    GridRow {
                        MetricSummary(title: "Weight", value: presentation.weightTitle)
                        MetricSummary(title: "Contribution", value: presentation.contributionTitle, detail: "Signed current ensemble input")
                        MetricSummary(title: "Progress", value: presentation.progressTitle)
                    }
                    GridRow {
                        MetricSummary(title: "Generation", value: presentation.strategy.generation.formatted())
                        if let complexity = presentation.strategy.complexity {
                            MetricSummary(title: "Complexity", value: complexity.formatted())
                        }
                    }
                }

                GroupBox("Evidence status") {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(presentation.promotionTitle, systemImage: "flag.checkered")
                        Label(presentation.causalAuditTitle, systemImage: "checkmark.shield")
                        Label(presentation.noRepaintTitle, systemImage: "arrow.trianglehead.2.clockwise.rotate.90")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(presentation.statusAccessibilityLabel)
                }

                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 12) {
                        metricGroup("Development", values: presentation.strategy.developmentMetrics)
                            .frame(minWidth: 220)
                        metricGroup("Sealed final", values: presentation.strategy.finalTestMetrics)
                            .frame(minWidth: 220)
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        metricGroup("Development", values: presentation.strategy.developmentMetrics)
                        metricGroup("Sealed final", values: presentation.strategy.finalTestMetrics)
                    }
                }

                if !chartPoints.isEmpty {
                    AccessibleChartContainer(
                        title: "Development and sealed-final evidence",
                        summary: "Development and untouched final-test metrics are presented separately.",
                        rows: chartPoints.map {
                            AccessibleChartRow(label: "\($0.period) \($0.metric)", value: ResearchFormatting.metric($0.value))
                        }
                    ) {
                        Chart(chartPoints) { point in
                            BarMark(
                                x: .value("Metric", point.metric),
                                y: .value("Value", point.value)
                            )
                            .foregroundStyle(by: .value("Period", point.period))
                            .position(by: .value("Period", point.period))
                        }
                        .frame(height: 180)
                    }
                }

                coverage

                if !presentation.warnings.isEmpty {
                    GroupBox("Warnings") {
                        VStack(alignment: .leading, spacing: 7) {
                            ForEach(presentation.warnings, id: \.self) {
                                Label($0, systemImage: "exclamationmark.triangle")
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                    }
                }

                Text(presentation.uncertaintyDisclosure)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(20)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .accessibilityIdentifier(StrategyLabAccessibility.detail)
    }

    private func metricPoint(
        _ period: String,
        _ metric: String,
        _ key: String,
        _ values: [String: Double?]
    ) -> StrategyMetricPoint? {
        guard let nested = values[key], let value = nested else { return nil }
        return StrategyMetricPoint(period: period, metric: metric, value: value)
    }

    private func metricGroup(_ title: String, values: [String: Double?]) -> some View {
        GroupBox(title) {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                metricRow("Sharpe", key: "sharpe", values: values)
                metricRow("Max drawdown", key: "maximum_drawdown", values: values, percentage: true)
                metricRow("Trades", key: "trade_count", values: values)
                metricRow("Cost survival", key: "doubled_cost_return", values: values, percentage: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private func metricRow(
        _ title: String,
        key: String,
        values: [String: Double?],
        percentage: Bool = false
    ) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            let nested = values[key]
            let value = nested == nil ? nil : nested!
            Text(percentage ? ResearchFormatting.percentage(value) : ResearchFormatting.metric(value))
                .monospacedDigit()
        }
    }

    @ViewBuilder private var coverage: some View {
        GroupBox("Coverage provenance") {
            VStack(alignment: .leading, spacing: 7) {
                Label(presentation.coverageTitle, systemImage: "externaldrive.badge.checkmark")
                if let coverage = presentation.coverage {
                    LabeledContent("Requested") {
                        Text("\(coverage.requestedStart.formatted(date: .abbreviated, time: .standard)) – \(coverage.requestedEnd.formatted(date: .abbreviated, time: .standard))")
                    }
                    LabeledContent("Covered through") {
                        if let end = coverage.coverageEnd {
                            Text(end.formatted(date: .abbreviated, time: .standard))
                        } else {
                            Text("Unavailable")
                        }
                    }
                    Label(
                        coverage.complete ? "Coverage complete" : "Coverage has \(coverage.gaps.count) disclosed gaps",
                        systemImage: coverage.complete ? "checkmark.circle" : "exclamationmark.triangle"
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }
}
