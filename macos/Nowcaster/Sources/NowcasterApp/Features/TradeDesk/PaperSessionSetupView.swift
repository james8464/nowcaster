import SwiftUI

struct PaperSessionSetupView: View {
    let model: AppModel
    let settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Set Up Paper Desk").font(.title2)
            Text("Retain Bitcoin and Ether public spot observations in a local research folder. Setup does not start collection.")
            Text("Existing folders and calendar evidence can be selected from the Trade Desk’s Data menu.").foregroundStyle(.secondary)
            if let directory = model.livePaperSignals.directory {
                LabeledContent("Current desk", value: directory.lastPathComponent)
            }
            if let message = model.livePaperSignals.message { Text(message).textSelection(.enabled) }
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                if model.livePaperSignals.isBusy { ProgressView().controlSize(.small) }
                Button("Set Up Paper Desk") {
                    Task { await model.livePaperSignals.createOrResumeDesk(sourceRoot: settings.configuration.projectRoot,
                                                                          sourcePython: settings.configuration.pythonExecutable) }
                }.disabled(model.livePaperSignals.isRunning || model.livePaperSignals.isBusy)
                    .accessibilityIdentifier("paperSignals.setup")
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 480)
    }
}

struct PaperResearchHelpView: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("About Paper Research").font(.title2)
            Text("The connected desk covers BTC/USDT and ETH/USDT Binance spot, studying long entries or standing aside. Other assets in Markets are imported or demo data.")
            Text("Set up a desk, start collection, and import current source-attributed calendar evidence. Missing or expired coverage means stand aside. A trend alone cannot publish research levels.")
            Text("New desks retain three fixed hypotheses: EMA + ADX trend, Donchian breakout and VWAP continuation. Whipsaws, false breakouts and costs can erase apparent gains. Older folders retain their original rules.")
            Text("Candidate evidence uses the registered schedule and cost checks. Background learning searches training data; a locked proposal needs a separate prospective evaluation. An inspected final holdout cannot become unseen again.")
            Text("These experimental hypotheses have not established profitability. No accounts are connected and no orders are placed.")
            HStack { Spacer(); Button("Close") { dismiss() }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width: 540).textSelection(.enabled)
    }
}
