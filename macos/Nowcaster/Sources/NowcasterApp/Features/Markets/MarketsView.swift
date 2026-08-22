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
            .width(min: 105, ideal: 125, max: 145)
            TableColumn("Last") { instrument in
                Text(ResearchFormatting.currency(instrument.lastPrice)).monospacedDigit()
            }
            .width(min: 78, ideal: 86, max: 96)
        }
        .accessibilityIdentifier("markets.table")
    }

}
