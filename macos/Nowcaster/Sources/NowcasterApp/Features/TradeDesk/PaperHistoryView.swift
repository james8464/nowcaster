import SwiftUI

struct PaperHistoryView: View {
    let service: LivePaperSignalService
    var body: some View {
        List {
            Text("Retained paper hypotheses and collection events. Outcomes are historical, not current signals or executable fills.")
                .foregroundStyle(.secondary).accessibilityIdentifier("history.explanation")
            Section("Completed hypotheses") {
                let rows = TradeDeskPresentation.history(evidence: service.decisionEvidence)
                if rows.isEmpty { Text("No validated completed outcomes loaded.").foregroundStyle(.secondary) }
                ForEach(rows) { row in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(row.symbol) · \(row.outcome)").fontWeight(.semibold)
                        Text(row.completedAt.formatted(date: .abbreviated, time: .standard))
                        Text("Realized costs: \(row.costs) · Net result: Unavailable").foregroundStyle(.secondary)
                    }.padding(.vertical, 6).accessibilityElement(children: .combine)
                }
            }
            Section("Evidence and gaps") {
                if service.events.isEmpty { Text("No retained events loaded.").foregroundStyle(.secondary) }
                ForEach(service.events.reversed()) { event in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(event.kind.researchTitle).fontWeight(.medium)
                        Text(event.at.formatted(date: .abbreviated, time: .standard))
                        if let detail = event.detail { Text(detail.researchTitle).foregroundStyle(.secondary) }
                    }.accessibilityElement(children: .combine)
                }
            }
        }.accessibilityIdentifier("history.list").textSelection(.enabled)
    }
}
