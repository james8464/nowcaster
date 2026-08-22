import Charts
import SwiftUI

struct EarningsDetailView: View {
    let forecast: EarningsSnapshot
    private var detail: EarningsDetailModel { EarningsDetailModel(forecast: forecast) }

    private var comparisons: [(String, Double)] {
        var rows = [("Model forecast", forecast.forecastRevenue), (detail.expectationTitle, forecast.expectationRevenue)]
        if let actual = forecast.actualRevenue { rows.append(("Reported actual", actual)) }
        return rows
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("\(forecast.companyId) · \(forecast.fiscalQuarter)").font(.largeTitle.weight(.semibold))
                Label(detail.expectationTitle, systemImage: "exclamationmark.bubble")
                    .foregroundStyle(.orange)
                AccessibleChartContainer(
                    title: "Revenue comparison",
                    summary: "Model forecast compared with the explicitly labelled expectation source and actual, when available.",
                    rows: comparisons.map { AccessibleChartRow(label: $0.0, value: ResearchFormatting.compactNumber($0.1)) }
                ) {
                    Chart(comparisons, id: \.0) { row in
                        BarMark(x: .value("Revenue", row.1), y: .value("Series", row.0))
                    }
                    .frame(height: 180)
                }
                HStack(alignment: .top, spacing: 28) {
                    MetricSummary(title: "Forecast", value: ResearchFormatting.compactNumber(forecast.forecastRevenue))
                    MetricSummary(title: "Expectation", value: ResearchFormatting.compactNumber(forecast.expectationRevenue))
                    MetricSummary(title: "Variant", value: ResearchFormatting.percentage(forecast.variant))
                }
                GroupBox("Point-in-time context") {
                    Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 8) {
                        GridRow { Text("Forecast cutoff"); Text(forecast.forecastCutoffDate, style: .date) }
                        GridRow { Text("Event basis"); Text(forecast.earningsDate, style: .date) }
                        GridRow { Text("Model"); Text("\(forecast.modelName) · \(forecast.ablation)") }
                        GridRow { Text("Expectation source"); Text(detail.expectationTitle) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }
                Text("The demo expectation is a historical seasonal proxy and must not be interpreted as archived sell-side estimates.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: 720, alignment: .leading)
        }
    }
}
