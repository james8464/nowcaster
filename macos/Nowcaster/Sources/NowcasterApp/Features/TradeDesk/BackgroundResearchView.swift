import SwiftUI

struct BackgroundResearchView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    var body: some View {
        Form {
            Section { PaperSessionHeader(model: model, settings: settings) }
            Section("Background learning") {
                Toggle("Enable background learning", isOn: Binding(get: { model.paperSession.preferences.learningEnabled },
                    set: { enabled in Task { await model.paperSession.setLearningEnabled(enabled) } }))
                    .accessibilityIdentifier("research.learning")
                Text("Training creates proposals for a separate prospective paper evaluation. It never replaces a registered strategy.")
                if let status = model.backgroundResearch.status {
                    LabeledContent("Activity", value: status.reason.researchTitle)
                    LabeledContent("Attempts retained", value: String(status.attemptCount))
                    LabeledContent("Failures retained", value: String(status.failureCount))
                    LabeledContent("Current batch attempts", value: "\(status.batchAttemptCount) / 100")
                    LabeledContent("Next eligible time", value: status.nextEligibleAt ?? "Awaiting eligible observations")
                    LabeledContent("Checkpoint", value: status.lastCheckpoint ?? "None yet")
                } else {
                    Text(model.backgroundResearch.message ?? "No background training has started.").foregroundStyle(.secondary)
                }
            }
            Section("Resources") {
                Text("\(model.paperSession.preferences.resourceProfile.rawValue.capitalized) · \(model.paperSession.preferences.resourceProfile.workers(cores: ProcessInfo.processInfo.activeProcessorCount)) workers · one numerical thread per worker")
                    .accessibilityIdentifier("research.resources")
                PaperResourcePicker(model: model)
                Text("Profiles change execution capacity only. Thermal, memory, disk, low-power and collector checks can pause dispatch.")
                if let reason = model.backgroundResearch.resourcePauseReason { Text(reason) }
                Button("Retry Research") { Task { await model.paperSession.retryResearch() } }
                    .disabled(!model.livePaperSignals.isRunning || !model.paperSession.preferences.learningEnabled || model.paperSession.state == .starting)
                    .accessibilityIdentifier("research.retry")
            }
            Section("Schedule") {
                Text("At most one new batch per asset per UTC day, with up to 100 candidate attempts and a new eligible data fingerprint.")
                Text("Closing the window keeps a started session running. Quit stops app-owned work. Sleep or offline time interrupts collection; missed intervals remain gaps.")
            }
        }.formStyle(.grouped)
    }
}

struct PaperResourcePicker: View {
    let model: AppModel
    var body: some View {
        Picker("Resource profile", selection: Binding(get: { model.paperSession.preferences.resourceProfile },
            set: { value in Task { await model.paperSession.setResourceProfile(value) } })) {
            ForEach(PaperSessionPreferences.ResourceProfile.allCases, id: \.self) { profile in
                Text(profile.rawValue.capitalized).tag(profile)
            }
        }.accessibilityIdentifier("settings.resources")
    }
}
