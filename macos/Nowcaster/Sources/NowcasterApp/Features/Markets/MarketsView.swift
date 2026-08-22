import SwiftUI

struct MarketsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot
    @State private var sortOrder = [KeyPathComparator(\InstrumentSnapshot.symbol)]

    private var sortedInstruments: [InstrumentSnapshot] {
        snapshot.instruments.sorted(using: sortOrder)
    }

    var body: some View {
        Table(sortedInstruments, selection: $model.selectedInstrumentID, sortOrder: $sortOrder) {
            TableColumn("Symbol", value: \.symbol) { instrument in
                VStack(alignment: .leading, spacing: 1) {
                    Text(instrument.symbol).fontWeight(.medium)
                    Text(instrument.displayName).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .width(min: 120, ideal: 170)
            TableColumn("Asset") { instrument in
                Label(
                    instrument.assetClass == .crypto ? "Crypto" : "Equity",
                    systemImage: instrument.assetClass == .crypto ? "bitcoinsign.circle" : "building.columns"
                )
            }
            .width(min: 80, ideal: 100)
            TableColumn("Last") { instrument in
                Text(ResearchFormatting.currency(instrument.lastPrice)).monospacedDigit()
            }
            .width(min: 85, ideal: 100)
            TableColumn("Day") { instrument in
                directionalValue(instrument.dailyReturn)
            }
            .width(min: 72, ideal: 84)
            TableColumn("Week") { instrument in
                directionalValue(instrument.weeklyReturn)
            }
            .width(min: 72, ideal: 84)
            TableColumn("Realized vol") { instrument in
                Text(ResearchFormatting.percentage(instrument.realizedVolatility, precision: 0)).monospacedDigit()
            }
            .width(min: 85, ideal: 100)
            TableColumn("Trend") { instrument in
                Label(
                    instrument.trendRegime.capitalized,
                    systemImage: instrument.trendRegime == "uptrend" ? "arrow.up.right" : "arrow.down.right"
                )
            }
            .width(min: 95, ideal: 115)
            TableColumn("Fresh through") { instrument in
                if let freshness = instrument.freshnessDate {
                    Text(freshness, style: .date)
                } else {
                    Text("Unavailable").foregroundStyle(.secondary)
                }
            }
            .width(min: 100, ideal: 120)
        }
        .accessibilityIdentifier("markets.table")
    }

    private func directionalValue(_ value: Double?) -> some View {
        let positive = (value ?? 0) >= 0
        return Label(
            ResearchFormatting.percentage(value),
            systemImage: value == nil ? "minus" : (positive ? "arrow.up.right" : "arrow.down.right")
        )
        .foregroundStyle(value == nil ? Color.secondary : (positive ? .green : .red))
        .monospacedDigit()
    }
}
