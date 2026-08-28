import SwiftUI

struct DeepResearchConfigurationView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    let context: SelectedStrategyResearchContext
    @Environment(\.dismiss) private var dismiss
    @State private var resourceProfile = ProcessInfo.processInfo.isLowPowerModeEnabled
        ? DeepResearchResourceProfile.balanced
        : DeepResearchResourceProfile.performance
    @State private var customWorkers = DeepResearchResourcePolicy.maximumWorkerCount(
        activeProcessors: ProcessInfo.processInfo.activeProcessorCount
    )
    @State private var continuous = true
    @State private var evaluationBudget = 1_000
    @State private var useTimeLimit = false
    @State private var timeLimitHours = 8
    @State private var seed = 42

    private var workers: Int {
        resourceProfile.workerCount(
            activeProcessors: ProcessInfo.processInfo.activeProcessorCount,
            customWorkers: customWorkers
        )
    }

    var body: some View {
        Form {
            Section("Scope") {
                LabeledContent("Asset", value: "\(context.asset.symbol) · \(context.asset.interval.rawValue)")
                LabeledContent("Source", value: "\(context.asset.provider.rawValue)/\(context.asset.feed)")
                LabeledContent("Strategies", value: context.strategyIDs.joined(separator: ", "))
            }

            Section("Compute") {
                Picker("Resource profile", selection: $resourceProfile) {
                    Text("Performance — reserve two cores").tag(DeepResearchResourceProfile.performance)
                    Text("Balanced — reserve three cores").tag(DeepResearchResourceProfile.balanced)
                    Text("Efficient — use half the cores").tag(DeepResearchResourceProfile.efficient)
                    Text("Custom").tag(DeepResearchResourceProfile.custom)
                }
                if resourceProfile == .custom {
                    Stepper(
                        "Workers: \(workers)",
                        value: $customWorkers,
                        in: 1 ... DeepResearchResourcePolicy.maximumWorkerCount(
                            activeProcessors: ProcessInfo.processInfo.activeProcessorCount
                        )
                    )
                } else {
                    LabeledContent("Workers", value: workers.formatted())
                }
                Text("Nowcaster reserves at least two logical processors for live monitoring, macOS, and the interface. It limits numerical libraries to one thread per worker and pauses under serious thermal pressure.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Search budget") {
                Toggle("Run continuously until I stop it", isOn: $continuous)
                if !continuous {
                    Stepper("Candidate attempts: \(evaluationBudget)", value: $evaluationBudget, in: 100 ... 100_000, step: 100)
                }
                Toggle("Set a time limit", isOn: $useTimeLimit)
                if useTimeLimit {
                    Stepper("Hours: \(timeLimitHours)", value: $timeLimitHours, in: 1 ... 168)
                }
                Stepper("Reproducible seed: \(seed)", value: $seed, in: 0 ... Int.max)
            }

            Section {
                Label(
                    "Deep Research searches historical data and a sealed holdout. Results remain hypothetical, cannot guarantee future profit, and never unlock live trading.",
                    systemImage: "exclamationmark.shield"
                )
                .font(.footnote)
                .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 620, height: 580)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Start Deep Research") { start() }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier(StrategyLabAccessibility.deepResearchStartButton)
            }
        }
    }

    private func start() {
        let configuration = settings.configuration
        let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let runID = "deep-\(UUID().uuidString.lowercased())"
        let request = DeepResearchRequest(
            strategyIDs: context.strategyIDs,
            asset: context.asset,
            workers: workers,
            evaluationBudget: continuous ? nil : evaluationBudget,
            continuous: continuous,
            timeBudgetSeconds: useTimeLimit ? timeLimitHours * 3_600 : nil,
            seed: seed,
            runID: runID,
            controlDirectory: configuration.projectRoot.appending(path: "data/deep-research-control", directoryHint: .isDirectory),
            controlNonce: token,
            resumeRunID: nil
        )
        dismiss()
        Task { await model.run(.deepResearch(request), configuration: configuration) }
    }
}
