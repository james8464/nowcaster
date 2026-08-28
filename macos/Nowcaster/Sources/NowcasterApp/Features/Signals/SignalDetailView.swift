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
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 140), alignment: .leading)],
                    alignment: .leading,
                    spacing: 18
                ) {
                    MetricSummary(
                        title: "Calibrated outcome",
                        value: ResearchFormatting.probability(signal.calibratedProbability),
                        detail: signal.calibratedProbability == nil ? "Insufficient calibration" : "Not a profit probability"
                    )
                    MetricSummary(
                        title: "Probability range",
                        value: ResearchFormatting.probabilityRange(
                            lower: signal.probabilityLowerBound,
                            upper: signal.probabilityUpperBound
                        ),
                        detail: "Uncertainty interval"
                    )
                    MetricSummary(
                        title: "Lower net edge",
                        value: ResearchFormatting.percentage(signal.lowerNetEdge, precision: 2),
                        detail: "After modeled costs and uncertainty"
                    )
                    MetricSummary(
                        title: "Evidence confidence",
                        value: signal.confidenceScore?.formatted(.number.precision(.fractionLength(0))) ?? "—",
                        detail: "Not a return assurance"
                    )
                    MetricSummary(title: "Eligibility", value: signal.eligibility.replacingOccurrences(of: "_", with: " ").capitalized)
                }
                if hasAccuracyEvidence {
                    accuracyEvidence
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

    private var hasAccuracyEvidence: Bool {
        [
            signal.provider,
            signal.probabilityDefinition,
            signal.driftStatus,
            signal.coverageStatus,
        ].contains { $0 != nil } || signal.lowerNetEdge != nil || signal.brierScore != nil
    }

    private var accuracyEvidence: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                DisclosureGroup {
                    evidenceGrid {
                        evidenceRow("Meaning", signal.probabilityDefinition ?? "Outcome definition unavailable")
                        evidenceRow(
                            "Range",
                            ResearchFormatting.probabilityRange(
                                lower: signal.probabilityLowerBound,
                                upper: signal.probabilityUpperBound
                            )
                        )
                        evidenceRow("Calibration sample", calibrationSample)
                        evidenceRow("Brier score", ResearchFormatting.metric(signal.brierScore))
                        evidenceRow("Calibration error", ResearchFormatting.percentage(signal.expectedCalibrationError))
                        evidenceRow("Selective coverage", ResearchFormatting.percentage(signal.coverageRatio))
                    }
                    .padding(.top, 8)
                } label: {
                    Label("Probability quality", systemImage: "scope")
                }
                Divider()
                DisclosureGroup {
                    evidenceGrid {
                        evidenceRow("Gross edge", ResearchFormatting.percentage(signal.grossEdge, precision: 2))
                        evidenceRow("Estimated cost", ResearchFormatting.percentage(signal.estimatedCost, precision: 2))
                        evidenceRow("Lower net edge", ResearchFormatting.percentage(signal.lowerNetEdge, precision: 2))
                    }
                    .padding(.top, 8)
                } label: {
                    Label("Cost-adjusted edge", systemImage: "minus.forwardslash.plus")
                }
                Divider()
                DisclosureGroup {
                    evidenceGrid {
                        evidenceRow("Source", sourceDescription)
                        evidenceRow("Product", marketDescription)
                        evidenceRow("Model age", ResearchFormatting.duration(seconds: signal.modelAgeSeconds))
                        evidenceRow("Latency", ResearchFormatting.milliseconds(signal.latencyMs))
                        evidenceRow("Regime", normalized(signal.regime))
                        evidenceRow("Drift", driftDescription)
                        evidenceRow("Coverage", normalized(signal.coverageStatus))
                    }
                    .padding(.top, 8)
                } label: {
                    Label("Data and model health", systemImage: "waveform.path.ecg.rectangle")
                }
            }
            .padding(.vertical, 4)
        } label: {
            Text("Accuracy evidence")
        }
        .accessibilityHint(ResearchFormatting.evidenceAccessibilitySummary(signal))
    }

    private var calibrationSample: String {
        guard let observations = signal.calibrationObservations else { return "—" }
        guard let effective = signal.calibrationEffectiveObservations else { return observations.formatted() }
        return "\(observations.formatted()) raw · \(effective.formatted(.number.precision(.fractionLength(1)))) effective"
    }

    private var sourceDescription: String {
        [signal.provider, signal.feed].compactMap { $0 }.joined(separator: "/").nonempty ?? "—"
    }

    private var marketDescription: String {
        [signal.venue, signal.product].compactMap { $0 }.joined(separator: " · ").nonempty ?? "—"
    }

    private var driftDescription: String {
        guard let status = signal.driftStatus else { return "—" }
        let score = signal.driftScore.map { " · score \(ResearchFormatting.metric($0))" } ?? ""
        return normalized(status) + score
    }

    private func normalized(_ value: String?) -> String {
        value?.replacingOccurrences(of: "_", with: " ").capitalized ?? "—"
    }

    private func evidenceGrid<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 7) { content() }
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func evidenceRow(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }
}

private extension String {
    var nonempty: String? { isEmpty ? nil : self }
}
