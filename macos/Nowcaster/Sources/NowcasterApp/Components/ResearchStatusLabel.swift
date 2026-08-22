import SwiftUI

struct ResearchStatusLabel: View {
    let title: String
    let systemImage: String
    let color: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.1), in: Capsule())
            .accessibilityLabel(title)
    }
}

extension ResearchPosture {
    var title: String {
        switch self {
        case .longResearch: "Long research"
        case .shortResearch: "Short research"
        case .abstain: "Abstain"
        case let .unknown(value): value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var systemImage: String {
        switch self {
        case .longResearch: "arrow.up.right"
        case .shortResearch: "arrow.down.right"
        case .abstain: "pause"
        case .unknown: "questionmark"
        }
    }

    var color: Color {
        switch self {
        case .longResearch: .green
        case .shortResearch: .red
        case .abstain, .unknown: .secondary
        }
    }
}

extension BacktestReadiness {
    var title: String {
        switch self {
        case .decisionReady: "Decision-ready research"
        case .researchOnly: "Research only"
        case .notReady: "Not ready"
        case let .unknown(value): value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var systemImage: String {
        switch self {
        case .decisionReady: "checkmark.seal"
        case .researchOnly: "flask"
        case .notReady: "exclamationmark.triangle"
        case .unknown: "questionmark.circle"
        }
    }

    var color: Color {
        switch self {
        case .decisionReady: .green
        case .researchOnly: .orange
        case .notReady: .red
        case .unknown: .secondary
        }
    }
}
