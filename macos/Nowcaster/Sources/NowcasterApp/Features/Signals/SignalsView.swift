import SwiftUI

struct SignalListModel: Sendable {
    let signals: [ResearchSignalSnapshot]

    var visibleSignals: [ResearchSignalSnapshot] {
        signals.sorted {
            let firstRank = $0.posture == .abstain ? 1 : 0
            let secondRank = $1.posture == .abstain ? 1 : 0
            if firstRank != secondRank { return firstRank < secondRank }
            let firstConfidence = $0.confidenceScore ?? 0
            let secondConfidence = $1.confidenceScore ?? 0
            if firstConfidence != secondConfidence { return firstConfidence > secondConfidence }
            return $0.decisionDate > $1.decisionDate
        }
    }
}

struct SignalsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot
    @State private var postureFilter: ResearchPosture?

    private var rows: [ResearchSignalSnapshot] {
        let ranked = SignalListModel(signals: snapshot.signals).visibleSignals
        guard let postureFilter else { return ranked }
        return ranked.filter { $0.posture == postureFilter }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Posture", selection: $postureFilter) {
                    Text("All postures").tag(ResearchPosture?.none)
                    Text("Long research").tag(ResearchPosture?.some(.longResearch))
                    Text("Short research").tag(ResearchPosture?.some(.shortResearch))
                    Text("Abstain").tag(ResearchPosture?.some(.abstain))
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 480)
                Spacer()
                Text("\(rows.count) signals").foregroundStyle(.secondary)
            }
            .padding()
            Divider()
            Table(rows, selection: $model.selectedSignalID) {
                TableColumn("Instrument") { signal in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(signal.instrumentId).fontWeight(.medium)
                        Text(signal.assetClass == .crypto ? "Crypto" : "Equity")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .width(min: 100, ideal: 130)
                TableColumn("Posture") { signal in
                    Label(signal.posture.title, systemImage: signal.posture.systemImage)
                        .foregroundStyle(signal.posture.color)
                }
                .width(min: 120, ideal: 140)
                TableColumn("Decision") { signal in Text(signal.decisionDate, style: .date) }
                    .width(min: 90, ideal: 110)
                TableColumn("Horizon") { signal in Text(signal.horizon) }
                    .width(min: 90, ideal: 120)
                TableColumn("Probability") { signal in
                    Text(ResearchFormatting.probability(signal.calibratedProbability)).monospacedDigit()
                }
                .width(min: 90, ideal: 105)
                TableColumn("Confidence") { signal in
                    Text(signal.confidenceScore?.formatted(.number.precision(.fractionLength(0))) ?? "—")
                        .monospacedDigit()
                }
                .width(min: 75, ideal: 90)
                TableColumn("Eligibility") { signal in Text(signal.eligibility.replacingOccurrences(of: "_", with: " ").capitalized) }
                    .width(min: 100, ideal: 120)
            }
            .accessibilityIdentifier("signals.table")
        }
    }
}
