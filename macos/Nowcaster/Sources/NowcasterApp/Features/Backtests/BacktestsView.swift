import SwiftUI

struct BacktestsView: View {
    @Bindable var model: AppModel
    let snapshot: NowcasterSnapshot

    var body: some View {
        Table(snapshot.backtests, selection: $model.selectedBacktestID) {
            TableColumn("Strategy") { backtest in
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(backtest.strategyName).fontWeight(.medium).lineLimit(2)
                        Text(backtest.assetClass == .crypto ? "Crypto" : "Equity event study")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: backtest.readiness.systemImage)
                        .foregroundStyle(backtest.readiness.color)
                        .accessibilityLabel(backtest.readiness.title)
                }
            }
            .width(min: 220, ideal: 270, max: 310)
        }
        .accessibilityIdentifier("backtests.table")
    }
}
