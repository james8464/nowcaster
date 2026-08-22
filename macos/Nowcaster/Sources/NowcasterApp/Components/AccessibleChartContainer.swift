import SwiftUI

struct AccessibleChartRow: Identifiable, Sendable {
    let label: String
    let value: String

    var id: String { "\(label)-\(value)" }
}

struct AccessibleChartContainer<Content: View>: View {
    let title: String
    let summary: String
    let rows: [AccessibleChartRow]
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content()
                .accessibilityLabel(title)
                .accessibilityValue(summary)
            Text(summary).font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("View chart data") {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 5) {
                    ForEach(rows) { row in
                        GridRow {
                            Text(row.label)
                            Text(row.value).monospacedDigit()
                        }
                    }
                }
                .font(.caption)
                .padding(.top, 6)
            }
        }
        .accessibilityElement(children: .contain)
    }
}
