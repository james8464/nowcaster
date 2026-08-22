import Charts
import SwiftUI

struct BacktestMetricGroup: Sendable {
    let period: String
    let metrics: [String: Double?]
}

struct BacktestDetailModel: Sendable {
    let backtest: BacktestSnapshot

    var verdictTitle: String {
        switch backtest.readiness {
        case .decisionReady: "Passed research gates"
        case .researchOnly: "Research only"
        case .notReady: "Not decision-ready"
        case .unknown: "Unclassified"
        }
    }

    var summary: String { backtest.verdict }
    var developmentMetrics: BacktestMetricGroup {
        BacktestMetricGroup(period: "Development", metrics: backtest.developmentMetrics)
    }
    var finalTestMetrics: BacktestMetricGroup {
        BacktestMetricGroup(period: "Final test", metrics: backtest.finalTestMetrics)
    }
}

struct BacktestDetailView: View {
    let backtest: BacktestSnapshot
    @State private var chartMode = ChartMode.performance

    private enum ChartMode: String, CaseIterable {
        case performance = "Performance"
        case risk = "Rolling risk"
        case exposure = "Exposure"
    }

    private var detail: BacktestDetailModel { BacktestDetailModel(backtest: backtest) }
    private var finalBoundary: Date? {
        guard backtest.equityCurve.count > 4 else { return nil }
        return backtest.equityCurve[Int(Double(backtest.equityCurve.count) * 0.8)].date
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(backtest.strategyName).font(.title.weight(.semibold))
                        Text(detail.verdictTitle).font(.title3)
                        Text(detail.summary).foregroundStyle(.secondary)
                    }
                    Spacer()
                    ResearchStatusLabel(
                        title: backtest.readiness.title,
                        systemImage: backtest.readiness.systemImage,
                        color: backtest.readiness.color
                    )
                }
                Divider()
                HStack(alignment: .top, spacing: 24) {
                    MetricSummary(title: "Trades", value: backtest.sampleSize.formatted())
                    MetricSummary(title: "Net return", value: metric(.percentage, "cumulative_return", in: backtest.fullMetrics))
                    MetricSummary(title: "Sharpe", value: metric(.number, "sharpe", in: backtest.fullMetrics))
                    MetricSummary(title: "Max drawdown", value: metric(.percentage, "maximum_drawdown", in: backtest.fullMetrics))
                }
                Picker("Chart", selection: $chartMode) {
                    ForEach(ChartMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 420)
                chart
                HStack(alignment: .top, spacing: 16) {
                    metricGroup(detail.developmentMetrics)
                    metricGroup(detail.finalTestMetrics)
                }
                .frame(maxWidth: .infinity)
                robustness
                sensitivity
                monthlyReturns
                HStack(alignment: .top, spacing: 16) {
                    textList("Assumptions", rows: backtest.assumptions, symbol: "checklist")
                    textList("Warnings", rows: backtest.warnings, symbol: "exclamationmark.triangle")
                }
                Text("Historical simulation only. Final-test evidence remains subject to regime change, data limitations, and live execution risk.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: 940, alignment: .leading)
        }
        .accessibilityIdentifier("backtest.detail")
    }

    @ViewBuilder private var chart: some View {
        switch chartMode {
        case .performance:
            AccessibleChartContainer(
                title: "Net equity curve",
                summary: "Development observations precede the marked final-test boundary.",
                rows: backtest.equityCurve.suffix(12).map {
                    AccessibleChartRow(label: $0.date.formatted(date: .abbreviated, time: .omitted), value: ResearchFormatting.metric($0.value))
                }
            ) {
                Chart {
                    ForEach(backtest.equityCurve) { point in
                        LineMark(x: .value("Date", point.date), y: .value("Net equity", point.value))
                    }
                    if let finalBoundary {
                        RuleMark(x: .value("Final test begins", finalBoundary))
                            .foregroundStyle(.orange)
                            .lineStyle(StrokeStyle(dash: [5, 4]))
                            .annotation(position: .top, alignment: .leading) { Text("Final test").font(.caption) }
                    }
                }
                .frame(height: 240)
            }
        case .risk:
            AccessibleChartContainer(
                title: "Rolling Sharpe and drawdown",
                summary: "Rolling risk-adjusted return is shown with the historical drawdown path.",
                rows: backtest.rollingSharpeCurve.suffix(12).map {
                    AccessibleChartRow(label: $0.date.formatted(date: .abbreviated, time: .omitted), value: ResearchFormatting.metric($0.value))
                }
            ) {
                Chart {
                    ForEach(backtest.rollingSharpeCurve) { point in
                        LineMark(x: .value("Date", point.date), y: .value("Rolling Sharpe", point.value))
                            .foregroundStyle(.blue)
                    }
                    RuleMark(y: .value("Zero", 0)).foregroundStyle(.secondary)
                }
                .frame(height: 220)
            }
        case .exposure:
            AccessibleChartContainer(
                title: "Gross exposure and turnover",
                summary: "Position size is volatility-targeted and does not exceed the declared gross cap.",
                rows: backtest.exposureCurve.suffix(12).map {
                    AccessibleChartRow(label: $0.date.formatted(date: .abbreviated, time: .omitted), value: ResearchFormatting.percentage($0.value))
                }
            ) {
                Chart(backtest.exposureCurve) { point in
                    AreaMark(x: .value("Date", point.date), y: .value("Gross exposure", point.value))
                        .foregroundStyle(.tint.opacity(0.22))
                }
                .frame(height: 220)
            }
        }
    }

    private func metricGroup(_ group: BacktestMetricGroup) -> some View {
        GroupBox(group.period) {
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 7) {
                metricRow("Cumulative return", .percentage, "cumulative_return", group.metrics)
                metricRow("Sharpe", .number, "sharpe", group.metrics)
                metricRow("Sortino", .number, "sortino", group.metrics)
                metricRow("Maximum drawdown", .percentage, "maximum_drawdown", group.metrics)
                metricRow("Hit rate", .percentage, "hit_rate", group.metrics)
                metricRow("Profit factor", .number, "profit_factor", group.metrics)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private enum MetricKind { case percentage, number }

    private func metric(_ kind: MetricKind, _ key: String, in metrics: [String: Double?]) -> String {
        let value = metrics[key] ?? nil
        return kind == .percentage ? ResearchFormatting.percentage(value) : ResearchFormatting.metric(value)
    }

    private func metricRow(_ title: String, _ kind: MetricKind, _ key: String, _ values: [String: Double?]) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(metric(kind, key, in: values)).monospacedDigit()
        }
    }

    private var robustness: some View {
        GroupBox("Robustness") {
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 7) {
                metricRow("Bootstrap positive probability", .percentage, "bootstrap_probability_positive", backtest.robustness)
                metricRow("Deflated Sharpe probability", .percentage, "deflated_sharpe_probability", backtest.robustness)
                metricRow("Profitable subperiods", .percentage, "profitable_subperiod_fraction", backtest.robustness)
                metricRow("Trials adjusted", .number, "trials_adjusted", backtest.robustness)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private var sensitivity: some View {
        GroupBox("Cost sensitivity") {
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 7) {
                GridRow {
                    Text("Scenario").fontWeight(.medium)
                    Text("Cost multiple").fontWeight(.medium)
                    Text("Net return").fontWeight(.medium)
                    Text("Sharpe").fontWeight(.medium)
                }
                Divider().gridCellColumns(4)
                ForEach(backtest.sensitivities) { item in
                    GridRow {
                        Text(item.scenario.replacingOccurrences(of: "_", with: " ").capitalized)
                        Text("\(item.costMultiplier.formatted(.number.precision(.fractionLength(0))))×")
                        Text(ResearchFormatting.percentage(item.metrics["cumulative_return"] ?? nil)).monospacedDigit()
                        Text(ResearchFormatting.metric(item.metrics["sharpe"] ?? nil)).monospacedDigit()
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private var monthlyReturns: some View {
        GroupBox("Recent monthly returns") {
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 6) {
                ForEach(backtest.monthlyReturns.suffix(18)) { point in
                    GridRow {
                        Text(point.date.formatted(.dateTime.year().month(.abbreviated)))
                        Label(
                            ResearchFormatting.percentage(point.value),
                            systemImage: point.value >= 0 ? "arrow.up.right" : "arrow.down.right"
                        )
                        .foregroundStyle(point.value >= 0 ? .green : .red)
                        .monospacedDigit()
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private func textList(_ title: String, rows: [String], symbol: String) -> some View {
        GroupBox(title) {
            VStack(alignment: .leading, spacing: 7) {
                ForEach(rows, id: \.self) { Label($0, systemImage: symbol) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }
}
