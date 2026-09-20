import SwiftUI

struct ResearchRoundView: View {
    let snapshot: ResearchRoundSnapshot?
    let loadMessage: String?

    private var presentation: ResearchRoundPresentation { ResearchRoundPresentation(snapshot: snapshot) }

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Label(presentation.title, systemImage: "flask")
                    .font(.headline)
                Text(presentation.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let snapshot {
                    LabeledContent("Status", value: presentation.statusTitle)
                    LabeledContent("Provider health", value: presentation.providerHealthTitle)
                    LabeledContent("Protocol", value: snapshot.protocolHash.prefix(12) + "…")
                    if let abstentionTitle = presentation.abstentionTitle {
                        ContentUnavailableView(
                            "Stand aside",
                            systemImage: "pause.circle",
                            description: Text(abstentionTitle)
                        )
                    } else {
                        candidateList(snapshot.candidates)
                    }
                } else {
                    ContentUnavailableView(
                        "No Research Round 2 report",
                        systemImage: "doc.questionmark",
                        description: Text(loadMessage ?? presentation.abstentionTitle ?? "No retained research report is available.")
                    )
                }
            }
        }
        .accessibilityIdentifier("strategyLab.researchRound")
    }

    @ViewBuilder private func candidateList(_ candidates: [ResearchRoundCandidate]) -> some View {
        if candidates.isEmpty {
            ContentUnavailableView(
                "No candidate evidence",
                systemImage: "chart.line.flattrend.xyaxis",
                description: Text("The round has no bounded candidate result to present."))
        } else {
            ForEach(candidates) { candidate in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(candidate.symbol) · \(candidate.strategyID)").fontWeight(.medium)
                    LabeledContent("Direction", value: candidate.direction == .long ? "Long research only" : "Stand aside")
                    LabeledContent("Coverage", value: candidate.sealedMetrics.coverage)
                    LabeledContent("Recorded reasons", value: candidate.reasons.joined(separator: " · "))
                }
                .padding(.vertical, 4)
            }
        }
    }
}
