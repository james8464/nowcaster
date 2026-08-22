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
                .pickerStyle(.menu)
                .frame(width: 190)
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
                .width(min: 76, ideal: 84, max: 96)
                TableColumn("Posture") { signal in
                    Label(signal.posture.compactTitle, systemImage: signal.posture.systemImage)
                        .foregroundStyle(signal.posture.color)
                        .accessibilityLabel(signal.posture.accessibilityDescription)
                }
                .width(min: 86, ideal: 96, max: 106)
            }
            .accessibilityIdentifier("signals.table")
        }
    }
}

private extension ResearchPosture {
    var compactTitle: String {
        switch self {
        case .longResearch: "Long"
        case .shortResearch: "Short"
        case .abstain: "Abstain"
        case let .unknown(value): value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}
