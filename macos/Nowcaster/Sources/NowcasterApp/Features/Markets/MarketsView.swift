import SwiftUI

struct MarketsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot
    @State private var sortOrder = [KeyPathComparator(\InstrumentSnapshot.symbol)]

    private var sortedInstruments: [InstrumentSnapshot] {
        snapshot.instruments.sorted(using: sortOrder)
    }

    var body: some View {
        GeometryReader { geometry in
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
            if geometry.size.width >= 400 {
                TableColumn("Eligibility") { instrument in
                    let context = ContextualResearchPresentation(evidence: instrument)
                    Label(context.eligibilityTitle, systemImage: context.eligibilitySymbol)
                        .lineLimit(1)
                        .help(context.eligibilityTitle)
                }
                .width(min: 130, ideal: 145)
            }
            if geometry.size.width >= 580 {
                TableColumn("Market conditions") { instrument in
                    Text(ContextualResearchPresentation(evidence: instrument).regimeTitle)
                        .lineLimit(1)
                }
                .width(min: 130, ideal: 150)
            }
        }
        .accessibilityIdentifier("markets.table")
        }
    }

}
