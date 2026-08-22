import SwiftUI

struct BacktestsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot

    var body: some View {
        Table(snapshot.backtests, selection: $model.selectedBacktestID) {
            TableColumn("Strategy") { backtest in
                VStack(alignment: .leading, spacing: 1) {
                    Text(backtest.strategyName).fontWeight(.medium)
                    Text(backtest.assetClass == .crypto ? "Crypto" : "Equity event study")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .width(min: 190, ideal: 260)
            TableColumn("Readiness") { backtest in
                Label(backtest.readiness.title, systemImage: backtest.readiness.systemImage)
                    .foregroundStyle(backtest.readiness.color)
            }
            .width(min: 130, ideal: 160)
            TableColumn("Sample") { backtest in Text(backtest.sampleSize.formatted()).monospacedDigit() }
                .width(min: 65, ideal: 80)
            TableColumn("Final Sharpe") { backtest in
                Text(ResearchFormatting.metric(backtest.finalTestMetrics["sharpe"] ?? nil)).monospacedDigit()
            }
            .width(min: 80, ideal: 100)
            TableColumn("Final return") { backtest in
                Text(ResearchFormatting.percentage(backtest.finalTestMetrics["cumulative_return"] ?? nil))
                    .monospacedDigit()
            }
            .width(min: 85, ideal: 105)
            TableColumn("Maximum drawdown") { backtest in
                Text(ResearchFormatting.percentage(backtest.fullMetrics["maximum_drawdown"] ?? nil))
                    .monospacedDigit()
            }
            .width(min: 110, ideal: 130)
        }
        .accessibilityIdentifier("backtests.table")
    }
}
