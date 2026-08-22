import Foundation
import Testing

@testable import NowcasterApp

private func monitorFixture() throws -> NowcasterSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    return try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
}

@Test func signalRankingPlacesActionableResearchBeforeAbstentions() throws {
    let rows = SignalListModel(signals: try monitorFixture().signals).visibleSignals
    let firstAbstention = try #require(rows.firstIndex { $0.posture == .abstain })
    #expect(!rows[..<firstAbstention].contains { $0.posture == .abstain })
}

@Test func earningsNeverLabelsProxyAsConsensus() throws {
    let forecast = try #require(try monitorFixture().earnings.first { $0.expectationMode == "expectation_proxy" })
    let detail = EarningsDetailModel(forecast: forecast)
    #expect(detail.expectationTitle == "Seasonal expectation proxy")
    #expect(!detail.expectationTitle.localizedCaseInsensitiveContains("consensus"))
}
