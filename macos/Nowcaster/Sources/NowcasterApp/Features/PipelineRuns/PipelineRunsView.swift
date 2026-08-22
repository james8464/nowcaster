import SwiftUI

struct PipelineRunsView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    let snapshot: NowcasterSnapshot

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Button("Rebuild all") { Task { await model.run(.rebuildAll, configuration: settings.configuration) } }
                    .buttonStyle(.borderedProminent)
                Button("Backtest") { Task { await model.run(.fullBacktest, configuration: settings.configuration) } }
                Button("Export snapshot") { Task { await model.run(.exportSnapshot, configuration: settings.configuration) } }
                Spacer()
                if model.isRunningJob {
                    ProgressView().controlSize(.small)
                    Text(model.progressEvents.last?.stage ?? "Running…").foregroundStyle(.secondary)
                }
            }
            .padding()
            Divider()
            Table(snapshot.pipelineRuns) {
                TableColumn("Status") { run in
                    Label(run.status.capitalized, systemImage: run.status == "success" ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(run.status == "success" ? .green : .red)
                }
                .width(min: 90, ideal: 105)
                TableColumn("Stage") { run in Text(run.command.replacingOccurrences(of: "_", with: " ").capitalized) }
                    .width(min: 130, ideal: 170)
                TableColumn("Mode") { run in Text(run.mode.capitalized) }
                    .width(70)
                TableColumn("Started") { run in Text(run.startedAt, style: .date) }
                    .width(min: 90, ideal: 105)
                TableColumn("Duration") { run in
                    if let endedAt = run.endedAt {
                        Text(endedAt.timeIntervalSince(run.startedAt).formatted(.number.precision(.fractionLength(1))) + "s")
                            .monospacedDigit()
                    } else {
                        Text("Running")
                    }
                }
                .width(80)
                TableColumn("Rows") { run in Text(run.rowCounts.values.reduce(0, +).formatted()).monospacedDigit() }
                    .width(75)
                TableColumn("Error") { run in
                    Text(run.errorSummary ?? "—")
                        .foregroundStyle(run.errorSummary == nil ? Color.secondary : Color.red)
                }
                    .width(min: 180, ideal: 300)
            }
        }
        .disabled(model.isRunningJob)
        .accessibilityIdentifier("pipelineRuns.view")
    }
}
