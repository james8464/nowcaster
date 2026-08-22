import Foundation
import Testing

@testable import NowcasterApp

private func backtestFixture() throws -> BacktestSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
    return try #require(snapshot.backtests.first { $0.readiness == .notReady })
}

@Test func notReadyBacktestDoesNotUsePositiveRecommendationCopy() throws {
    let model = BacktestDetailModel(backtest: try backtestFixture())
    #expect(model.verdictTitle == "Not decision-ready")
    #expect(!model.summary.localizedCaseInsensitiveContains("profitable strategy"))
}

@Test func finalTestMetricsAreSeparatedFromDevelopment() throws {
    let model = BacktestDetailModel(backtest: try backtestFixture())
    #expect(model.developmentMetrics.period == "Development")
    #expect(model.finalTestMetrics.period == "Final test")
    #expect(model.developmentMetrics.period != model.finalTestMetrics.period)
}
