import SwiftUI

struct DeepResearchRunPresentation: Identifiable, Sendable {
    let run: DeepResearchRunSnapshot

    var id: String { run.id }
    var outcomeTitle: String { run.outcome.researchTitle }
    var attemptsTitle: String {
        let budget = run.trialBudget.map(String.init) ?? "continuous"
        return "\(run.evaluatedAttempts) of \(budget) attempts"
    }
    var workerTitle: String { "\(run.resources.activeWorkers) active · \(run.resources.queuedTrials) queued" }
    var scoreTitle: String { ResearchFormatting.metric(run.championScore) }
    var failedGates: [String] { run.failedGates }
    var disclosure: String {
        "Hypothetical research evidence only. Historical simulation can fail in live markets; this is not a trade instruction and profit is not promised."
    }
}

struct DeepResearchWorkspaceView: View {
    @Bindable var model: AppModel
    let runs: [DeepResearchRunPresentation]

    private var selected: DeepResearchRunPresentation? {
        runs.first { $0.id == model.selectedDeepResearchRunID } ?? runs.first
    }

    var body: some View {
        if runs.isEmpty {
            EmptyStateView(
                title: "No Deep Research runs",
                systemImage: "cpu",
                description: "Start Deep Research to test challengers against frozen data, costs, stress scenarios, and a sealed final holdout."
            )
            .accessibilityIdentifier(StrategyLabAccessibility.deepResearchWorkspace)
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Picker("Deep Research run", selection: $model.selectedDeepResearchRunID) {
                        ForEach(runs) { run in
                            Text("\(run.run.symbol) · \(run.outcomeTitle)").tag(String?.some(run.id))
                        }
                    }
                    .frame(maxWidth: 440)

                    if let selected {
                        GroupBox {
                            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 10) {
                                GridRow { Text("Outcome").foregroundStyle(.secondary); Text(selected.outcomeTitle) }
                                GridRow { Text("Progress").foregroundStyle(.secondary); Text(selected.attemptsTitle) }
                                GridRow { Text("Generation").foregroundStyle(.secondary); Text(selected.run.generation.formatted()) }
                                GridRow { Text("Champion score").foregroundStyle(.secondary); Text(selected.scoreTitle) }
                                GridRow { Text("Resources").foregroundStyle(.secondary); Text(selected.workerTitle) }
                                GridRow { Text("Thermals").foregroundStyle(.secondary); Text(selected.run.resources.thermalState.capitalized) }
                                GridRow {
                                    Text("Final holdout").foregroundStyle(.secondary)
                                    Text(selected.run.finalTestStart.formatted(date: .abbreviated, time: .standard))
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        } label: {
                            Label("Evidence summary", systemImage: "checkmark.shield")
                        }

                        GroupBox("Failed reliability gates") {
                            VStack(alignment: .leading, spacing: 8) {
                                if selected.failedGates.isEmpty {
                                    Label("No failed gates recorded", systemImage: "checkmark.circle")
                                } else {
                                    ForEach(Array(selected.failedGates.enumerated()), id: \.offset) { _, gate in
                                        Label(gate, systemImage: "xmark.circle")
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }

                        Label(selected.disclosure, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .accessibilityLabel(selected.disclosure)
                    }
                }
                .padding()
            }
            .accessibilityIdentifier(StrategyLabAccessibility.deepResearchWorkspace)
        }
    }
}
