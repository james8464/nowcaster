import SwiftUI

struct SignalDetailView: View {
    let signal: ResearchSignalSnapshot

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(signal.instrumentId).font(.largeTitle.weight(.semibold))
                        Spacer()
                        ResearchStatusLabel(
                            title: signal.posture.title,
                            systemImage: signal.posture.systemImage,
                            color: signal.posture.color
                        )
                    }
                    Text("\(signal.horizon) · \(signal.decisionDate.formatted(date: .abbreviated, time: .omitted))")
                        .foregroundStyle(.secondary)
                }
                Divider()
                HStack(alignment: .top, spacing: 28) {
                    MetricSummary(
                        title: "Calibrated direction",
                        value: ResearchFormatting.probability(signal.calibratedProbability),
                        detail: signal.calibratedProbability == nil ? "Insufficient past calibration" : "Direction only"
                    )
                    MetricSummary(
                        title: "Evidence confidence",
                        value: signal.confidenceScore?.formatted(.number.precision(.fractionLength(0))) ?? "—",
                        detail: "Not a return assurance"
                    )
                    MetricSummary(title: "Eligibility", value: signal.eligibility.replacingOccurrences(of: "_", with: " ").capitalized)
                }
                detailSection("Evidence", text: signal.evidenceSummary, systemImage: "doc.text.magnifyingglass")
                detailSection("Catalyst", text: signal.catalyst, systemImage: "bolt")
                detailSection("Invalidation", text: signal.invalidation, systemImage: "xmark.circle")
                if !signal.reasons.isEmpty {
                    GroupBox("Why this may be ineligible") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(signal.reasons, id: \.self) { reason in
                                Label(reason, systemImage: "info.circle")
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                    }
                }
                Text("Research evidence only — no order is created from this posture.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: 680, alignment: .leading)
        }
        .navigationTitle(signal.instrumentId)
    }

    private func detailSection(_ title: String, text: String, systemImage: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: systemImage).font(.headline)
            Text(text).foregroundStyle(.secondary).textSelection(.enabled)
        }
    }
}
