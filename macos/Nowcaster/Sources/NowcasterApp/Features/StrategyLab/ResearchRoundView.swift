import SwiftUI
import UniformTypeIdentifiers

struct ResearchRoundView: View {
    @Bindable var model: AppModel
    @State private var showingReportImporter = false

    private var snapshot: ResearchRoundSnapshot? { model.researchRoundSnapshot }
    private var presentation: ResearchRoundPresentation { ResearchRoundPresentation(snapshot: snapshot) }

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Label(presentation.title, systemImage: "flask")
                    .font(.headline)
                Button("Open Research Round 2 Report", systemImage: "folder") {
                    showingReportImporter = true
                }
                .help("Open a retained paper-only Research Round 2 JSON report. This does not place orders or start monitoring.")
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
                        description: Text(model.researchRoundLoadMessage ?? presentation.abstentionTitle ?? "No retained research report is available.")
                    )
                }
            }
        }
        .accessibilityIdentifier("strategyLab.researchRound")
        .fileImporter(
            isPresented: $showingReportImporter,
            allowedContentTypes: [.json],
            allowsMultipleSelection: false
        ) { result in
            guard case let .success(urls) = result, let url = urls.first else { return }
            Task { await model.loadResearchRound(url: url) }
        }
    }

    @ViewBuilder private func candidateList(_ candidates: [ResearchRoundCandidate]) -> some View {
        if candidates.isEmpty {
            ContentUnavailableView(
                "No candidate evidence",
                systemImage: "chart.line.flattrend.xyaxis",
                description: Text("The round has no bounded candidate result to present."))
        } else {
            ForEach(candidates) { candidate in
                let candidatePresentation = ResearchRoundCandidatePresentation(candidate: candidate)
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(candidate.symbol) · \(candidate.strategyID)").fontWeight(.medium)
                    LabeledContent("Status", value: candidatePresentation.statusTitle)
                    LabeledContent("Direction", value: candidatePresentation.directionTitle)
                    LabeledContent("Coverage", value: candidate.sealedMetrics.coverage)
                    LabeledContent("Recorded reasons", value: candidatePresentation.reasonTitle)
                }
                .padding(.vertical, 4)
            }
        }
    }
}
