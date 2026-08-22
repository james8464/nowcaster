import Testing

@testable import NowcasterApp

@Test func destinationsHaveStableUniqueIdentifiers() {
    let ids = AppDestination.allCases.map(\.id)

    #expect(Set(ids).count == ids.count)
    #expect(AppDestination.today.title == "Today")
    #expect(AppDestination.backtests.symbolName == "chart.xyaxis.line")
}
