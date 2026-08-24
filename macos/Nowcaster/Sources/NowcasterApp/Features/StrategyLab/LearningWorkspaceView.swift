import SwiftUI

struct LearningProgressPresentation: Equatable, Sendable {
    let value: Double
    let title: String
    let isLive: Bool

    init(run: LearningRunPresentation, live: ActiveJobProgress?) {
        if let live, let progress = live.value {
            value = progress
            title = live.message
            isLive = true
        } else {
            value = run.progressValue
            title = run.progressTitle
            isLive = false
        }
    }
}

struct LearningWorkspaceView: View {
    @Bindable var model: AppModel
    let runs: [LearningRunPresentation]
    let emptyTitle: String
    let emptyDescription: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var selected: LearningRunPresentation? {
        runs.first { $0.id == model.selectedLearningRunID } ?? runs.first
    }

    var body: some View {
        if runs.isEmpty {
            EmptyStateView(title: emptyTitle, systemImage: "wand.and.stars", description: emptyDescription)
                .accessibilityIdentifier(StrategyLabAccessibility.learning)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ViewThatFits(in: .horizontal) {
                    HStack { runPicker; Spacer(); boundaryLabel }
                    VStack(alignment: .leading, spacing: 5) { runPicker; boundaryLabel }
                }

                if let selected {
                    ViewThatFits(in: .horizontal) {
                        wideSummary(selected)
                        compactSummary(selected)
                    }
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel(selected.accessibilityLabel)

                    Table(selected.run.trials) {
                        TableColumn("Rule") { trial in Text(trial.ruleText).lineLimit(2) }
                            .width(min: 220, ideal: 340)
                        TableColumn("Status") { trial in
                            Label(
                                trial.status.capitalized,
                                systemImage: trial.status == "succeeded" ? "checkmark.circle" : "exclamationmark.circle"
                            )
                        }
                        .width(100)
                        TableColumn("Fitness") { trial in Text(ResearchFormatting.metric(trial.fitness)).monospacedDigit() }
                            .width(75)
                        TableColumn("Complexity") { trial in Text(trial.complexity.formatted()).monospacedDigit() }
                            .width(80)
                        TableColumn("Evaluated") { trial in
                            Text(trial.evaluatedAt.formatted(date: .abbreviated, time: .standard))
                        }
                        .width(min: 140, ideal: 170)
                    }
                }
            }
            .padding()
            .accessibilityIdentifier(StrategyLabAccessibility.learning)
        }
    }

    private func wideSummary(_ selected: LearningRunPresentation) -> some View {
        HStack(alignment: .top, spacing: 18) {
            ruleSummary(selected).frame(minWidth: 420, alignment: .leading)
            Spacer()
            progressSummary(selected, width: 180)
        }
    }

    private func compactSummary(_ selected: LearningRunPresentation) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(selected.bestRule).font(.headline).lineLimit(3).fixedSize(horizontal: false, vertical: true)
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Label(selected.generationTitle, systemImage: "arrow.triangle.2.circlepath")
                    Label(selected.complexityTitle, systemImage: "point.3.filled.connected.trianglepath.dotted")
                }
                GridRow {
                    Label(selected.promotionTitle, systemImage: "eye")
                    Label(selected.noRepaintTitle, systemImage: "checkmark.shield")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            progressSummary(selected, width: nil)
        }
    }

    private func ruleSummary(_ selected: LearningRunPresentation) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(selected.bestRule).font(.headline).lineLimit(3)
            HStack(spacing: 12) {
                Label(selected.generationTitle, systemImage: "arrow.triangle.2.circlepath")
                Label(selected.complexityTitle, systemImage: "point.3.filled.connected.trianglepath.dotted")
                Label(selected.promotionTitle, systemImage: "eye")
                Label(selected.noRepaintTitle, systemImage: "checkmark.shield")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private func progressSummary(_ selected: LearningRunPresentation, width: CGFloat?) -> some View {
        let progress = LearningProgressPresentation(run: selected, live: model.activeJobProgress)
        return VStack(alignment: width == nil ? .leading : .trailing, spacing: 6) {
            Text(progress.title).monospacedDigit().lineLimit(2)
            ProgressView(value: progress.value)
                .frame(width: width)
                .accessibilityLabel(StrategyLabAccessibility.progressLabel)
                .accessibilityValue(progress.title)
            Text(progress.isLive ? "Live engine progress" : reduceMotion ? "Static progress" : "Bounded progress")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var runPicker: some View {
        Picker("Learning run", selection: $model.selectedLearningRunID) {
            ForEach(runs) { run in
                Text("\(run.run.learningRunId) · \(run.run.state.capitalized)")
                    .tag(String?.some(run.id))
            }
        }
        .frame(maxWidth: 360)
    }

    @ViewBuilder private var boundaryLabel: some View {
        if let selected {
            Text(selected.boundaryTitle)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
    }
}
