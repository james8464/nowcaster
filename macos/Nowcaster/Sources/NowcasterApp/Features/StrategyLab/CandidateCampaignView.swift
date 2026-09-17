import SwiftUI

enum CandidateCampaignStatus: Sendable {
    case unavailable
    case rejected
    case available
}

struct CandidateCampaignPresentation: Sendable {
    let assetName: String
    let status: CandidateCampaignStatus
    let reason: String

    var title: String { "Research only" }
    var detail: String { "\(assetName): \(reason)" }
    var isActionable: Bool { false }
    var symbolName: String {
        switch status {
        case .unavailable, .rejected: "exclamationmark.triangle"
        case .available: "doc.text.magnifyingglass"
        }
    }
}

struct CandidateCampaignView: View {
    let presentation: CandidateCampaignPresentation

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: presentation.symbolName)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 3) {
                Text(presentation.title).font(.headline)
                Text(presentation.detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding()
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(presentation.title). \(presentation.detail). No trading action is available.")
    }
}
