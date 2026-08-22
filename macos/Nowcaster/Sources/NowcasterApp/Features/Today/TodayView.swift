import SwiftUI

struct TodayView: View {
    let snapshot: NowcasterSnapshot
    let selectSignal: (ResearchSignalSnapshot) -> Void

    private var rankedSignals: [ResearchSignalSnapshot] {
        SignalListModel(signals: snapshot.signals).visibleSignals.filter { $0.posture != .abstain }.prefix(6).map { $0 }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Research briefing").font(.largeTitle.weight(.semibold))
                    Text(snapshot.metadata.sourcePosture)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                HStack(alignment: .top, spacing: 36) {
                    MetricSummary(title: "Instruments", value: "\(snapshot.overview.instrumentCount)")
                    MetricSummary(title: "Research signals", value: "\(snapshot.overview.signalCount)")
                    MetricSummary(title: "Forecasts", value: snapshot.overview.forecastCount.formatted())
                    MetricSummary(title: "Quality issues", value: "\(snapshot.overview.qualityIssueCount)")
                }

                if !snapshot.qualityIssues.isEmpty {
                    sectionHeader("Needs attention", systemImage: "exclamationmark.triangle")
                    ForEach(snapshot.qualityIssues.prefix(5)) { issue in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Image(systemName: issue.severity == "error" ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(issue.severity == "error" ? .red : .orange)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(issue.message)
                                Text("\(issue.stage) · \(issue.entityKey)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(issue.detectedAt, style: .relative).font(.caption).foregroundStyle(.secondary)
                        }
                        Divider()
                    }
                }

                sectionHeader("Highest-ranked research", systemImage: "waveform.path.ecg")
                if rankedSignals.isEmpty {
                    Text("No signal currently clears the declared evidence gate.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(rankedSignals) { signal in
                        Button {
                            selectSignal(signal)
                        } label: {
                            HStack(spacing: 12) {
                                ResearchStatusLabel(
                                    title: signal.posture.title,
                                    systemImage: signal.posture.systemImage,
                                    color: signal.posture.color
                                )
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(signal.instrumentId).font(.headline)
                                    Text(signal.evidenceSummary).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer()
                                Text(ResearchFormatting.probability(signal.calibratedProbability))
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        Divider()
                    }
                }

                sectionHeader("Research posture", systemImage: "info.circle")
                Text("Signals rank evidence for further investigation. They do not place orders or assure future returns. Backtest readiness is shown separately from model confidence.")
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
        }
        .accessibilityIdentifier("today.view")
    }

    private func sectionHeader(_ title: String, systemImage: String) -> some View {
        Label(title, systemImage: systemImage).font(.title3.weight(.semibold))
    }
}
