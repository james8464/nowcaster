import SwiftUI

/// A live research workspace, deliberately independent of the earnings/demo snapshot.
struct TradeDeskView: View {
    @Bindable var model: AppModel
    let settings: AppSettings

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Observe. Plan. Review.").font(.largeTitle.bold())
                    Text("Track market conditions, inspect a rule-based setup, and retain what happened next.")
                        .foregroundStyle(.secondary)
                }
                LivePaperSignalsView(model: model, settings: settings)
                DayTraderContextView(service: model.livePaperSignals)
                GroupBox("Coverage and research playbook") {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Bitcoin / USDT · Ether / USDT", systemImage: "bitcoinsign.circle")
                            .font(.headline)
                        Text("Public Binance spot data. This desk studies long entries or standing aside, not short selling. Stocks, ETFs, oil and futures are not connected to this desk.")
                        Divider()
                        Text("New desk: three fixed hypotheses per asset").font(.subheadline.bold())
                        playbook("EMA + ADX trend", "Looks for fast/slow moving-average agreement and trend strength. Can whipsaw in sideways markets.")
                        playbook("Donchian breakout", "Looks for a close beyond the previous 20-bar range. False breakouts and trading costs can erase the move.")
                        playbook("VWAP continuation", "Checks price and slope against the session's volume-weighted average. A trend is context, not a probability of profit.")
                        Text("One-minute research variants; 1/5/15-minute context and spread, volatility, calendar and freshness checks apply. Opening an older folder keeps its original rules.")
                            .font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
                }
                GroupBox("Before an entry setup can appear") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("1. Set up the desk, then start public-data collection. Keep the Mac awake and online; quitting stops this app-owned service.")
                        Text("2. Import current, source-attributed event-calendar evidence. Missing or expired coverage means stand aside—not an assumption that no news is scheduled.")
                        Text("3. Evaluate the retained candidate evidence. A new desk uses 90 training days, 30 validation days and 30 sealed-test days, plus coverage, cost and minimum-trade requirements. The live collector does not run that evaluation itself.")
                        Text("Only a candidate that meets those gates can produce an experimental entry zone, invalidation, target and expiry. There is no daily trade quota. The current software and these new hypotheses have not established profitability.")
                            .foregroundStyle(.secondary)
                    }.font(.callout).frame(maxWidth: .infinity, alignment: .leading).padding(4)
                }
            }.padding(24).frame(maxWidth: 1000)
                .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .accessibilityIdentifier("tradeDesk.workspace")
        .task {
            let directory = LivePaperSignalService.defaultDirectory
            if model.livePaperSignals.directory == nil,
               FileManager.default.fileExists(atPath: directory.appending(path: "protocol.json").path) {
                await model.livePaperSignals.open(directory: directory, sourceRoot: settings.configuration.projectRoot,
                                                  sourcePython: settings.configuration.pythonExecutable)
            }
        }
    }

    private func playbook(_ title: String, _ explanation: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.subheadline.weight(.semibold))
            Text(explanation).font(.callout).foregroundStyle(.secondary)
        }
    }
}
