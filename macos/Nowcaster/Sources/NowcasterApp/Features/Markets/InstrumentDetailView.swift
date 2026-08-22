import Charts
import SwiftUI

struct InstrumentDetailView: View {
    let instrument: InstrumentSnapshot
    @State private var periodDays = 180

    private var visibleHistory: [PricePoint] {
        Array(instrument.priceHistory.suffix(periodDays))
    }

    private var chartSummary: String {
        guard let first = visibleHistory.first, let last = visibleHistory.last else { return "No price history is available." }
        let change = last.close / first.close - 1
        return "From \(first.date.formatted(date: .abbreviated, time: .omitted)) to \(last.date.formatted(date: .abbreviated, time: .omitted)), \(instrument.symbol) changed \(ResearchFormatting.percentage(change))."
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(instrument.symbol).font(.largeTitle.weight(.semibold))
                    Text(instrument.displayName).foregroundStyle(.secondary)
                }
                HStack(alignment: .top, spacing: 24) {
                    MetricSummary(title: "Last", value: ResearchFormatting.currency(instrument.lastPrice))
                    MetricSummary(title: "Day", value: ResearchFormatting.percentage(instrument.dailyReturn))
                    MetricSummary(title: "Week", value: ResearchFormatting.percentage(instrument.weeklyReturn))
                    MetricSummary(title: "Realized vol", value: ResearchFormatting.percentage(instrument.realizedVolatility, precision: 0))
                }
                Picker("History", selection: $periodDays) {
                    Text("1M").tag(30)
                    Text("3M").tag(90)
                    Text("6M").tag(180)
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 260)
                AccessibleChartContainer(
                    title: "Adjusted daily close",
                    summary: chartSummary,
                    rows: visibleHistory.suffix(12).map {
                        AccessibleChartRow(label: $0.date.formatted(date: .abbreviated, time: .omitted), value: ResearchFormatting.currency($0.close))
                    }
                ) {
                    Chart(visibleHistory) { point in
                        AreaMark(
                            x: .value("Date", point.date),
                            yStart: .value("Baseline", visibleHistory.map(\.close).min() ?? 0),
                            yEnd: .value("Close", point.close)
                        )
                        .foregroundStyle(.tint.opacity(0.12))
                        LineMark(x: .value("Date", point.date), y: .value("Close", point.close))
                            .interpolationMethod(.catmullRom)
                            .foregroundStyle(.tint)
                    }
                    .chartYAxis { AxisMarks(position: .leading) }
                    .frame(height: 240)
                }
                GroupBox("Market context") {
                    Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 8) {
                        GridRow { Text("Asset class"); Text(instrument.assetClass == .crypto ? "Crypto" : "Equity") }
                        GridRow { Text("Trend regime"); Text(instrument.trendRegime.capitalized) }
                        GridRow {
                            Text("Data freshness")
                            if let date = instrument.freshnessDate { Text(date, style: .date) } else { Text("Unavailable") }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }
            }
            .padding(24)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .accessibilityIdentifier("instrument.detail")
    }
}
