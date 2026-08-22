import SwiftUI

struct DataQualityView: View {
    let snapshot: NowcasterSnapshot
    @State private var severity = "All"

    private var issues: [QualityIssueSnapshot] {
        guard severity != "All" else { return snapshot.qualityIssues }
        return snapshot.qualityIssues.filter { $0.severity.localizedCaseInsensitiveCompare(severity) == .orderedSame }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Severity", selection: $severity) {
                    ForEach(["All", "Error", "Warning", "Info"], id: \.self, content: Text.init)
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 340)
                Spacer()
                Label(snapshot.metadata.sourcePosture, systemImage: "checkmark.shield")
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .padding()
            Divider()
            if issues.isEmpty {
                EmptyStateView(
                    title: "No matching issues",
                    systemImage: "checkmark.shield",
                    description: "No data-quality issue matches the selected severity."
                )
            } else {
                Table(issues) {
                    TableColumn("Severity") { issue in
                        Label(issue.severity.capitalized, systemImage: severitySymbol(issue.severity))
                            .foregroundStyle(severityColor(issue.severity))
                    }
                    .width(min: 85, ideal: 100)
                    TableColumn("Stage") { issue in Text(issue.stage) }
                        .width(min: 110, ideal: 140)
                    TableColumn("Entity") { issue in Text(issue.entityKey).textSelection(.enabled) }
                        .width(min: 100, ideal: 140)
                    TableColumn("Rule") { issue in Text(issue.rule) }
                        .width(min: 110, ideal: 150)
                    TableColumn("Message") { issue in Text(issue.message).lineLimit(2) }
                        .width(min: 220, ideal: 360)
                    TableColumn("Detected") { issue in Text(issue.detectedAt, style: .relative) }
                        .width(min: 90, ideal: 110)
                }
            }
        }
        .accessibilityIdentifier("dataQuality.view")
    }

    private func severitySymbol(_ value: String) -> String {
        switch value.lowercased() {
        case "error": "xmark.octagon.fill"
        case "warning": "exclamationmark.triangle.fill"
        default: "info.circle"
        }
    }

    private func severityColor(_ value: String) -> Color {
        switch value.lowercased() {
        case "error": .red
        case "warning": .orange
        default: .secondary
        }
    }
}
