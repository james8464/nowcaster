import Foundation
import Testing

@testable import NowcasterApp

private func fixtureSnapshot() throws -> NowcasterSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    return try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
}

@Test @MainActor func globalSearchFindsSymbolsAndSelectsMarket() throws {
    let model = AppModel(snapshot: try fixtureSnapshot())
    model.searchText = "ETH"
    #expect(model.searchResults.map(\.symbol) == ["ETH-USD"])
    model.selectSearchResult(try #require(model.searchResults.first))
    #expect(model.destination == .markets)
    #expect(model.selectedInstrumentID == "ETH-USD")
}

@Test @MainActor func staleQualityIssuesArePrioritized() throws {
    let model = AppModel(snapshot: try fixtureSnapshot())
    #expect(model.snapshot != nil)
    #expect(model.dataModeLabel == "Demo snapshot")
}
