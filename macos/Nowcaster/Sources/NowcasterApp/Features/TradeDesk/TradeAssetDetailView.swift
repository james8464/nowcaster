import SwiftUI

struct TradeAssetDetailView: View {
    let model: AppModel
    let symbol: String
    let close: () -> Void
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { timeline in
            let row = TradeDeskPresentation.make(service: model.livePaperSignals, session: model.paperSession, now: timeline.date).assets.first { $0.symbol == symbol }
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        Text(symbol).fontWeight(.semibold)
                            .accessibilityIdentifier("tradeDesk.detail.title")
                            .accessibilityValue(symbol)
                        Spacer()
                        Button("Close Details", systemImage: "xmark") { close() }
                            .labelStyle(.iconOnly).accessibilityIdentifier("tradeDesk.closeDetail")
                    }
                    if let row {
                        LabeledContent("Source", value: row.source)
                        LabeledContent("Freshness", value: row.freshness)
                        LabeledContent("Trend", value: row.trend)
                        LabeledContent("Posture", value: row.posture)
                        Text(row.reason)
                        if let detail = row.detail {
                            Divider()
                            LabeledContent("Research entry zone", value: "\(detail.entryLow ?? "—") – \(detail.entryHigh ?? "—")")
                            LabeledContent("Invalidation", value: detail.invalidation ?? "—")
                            LabeledContent("Research target", value: detail.target ?? "—")
                            LabeledContent("Expires", value: detail.expiresAt.formatted(date: .omitted, time: .standard))
                            Text("Experimental paper hypothesis. These are not executable fills or qualified trading instructions.")
                        }
                    }
                    Divider()
                    Text("Trend describes observed direction. Publication also requires fresh receipts, calendar coverage and candidate evidence; it is not a probability of profit.")
                        .foregroundStyle(.secondary)
                }.padding(20).textSelection(.enabled)
        }
    }
}
