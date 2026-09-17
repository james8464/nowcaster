import Testing

@testable import NowcasterApp

@Test func unavailableCampaignExplainsMissingVerifiedData() {
    let presentation = CandidateCampaignPresentation(
        assetName: "WTI crude oil",
        status: .unavailable,
        reason: "WTI needs verified intraday contract data before research can begin"
    )

    #expect(presentation.title == "Research only")
    #expect(presentation.detail.contains("verified intraday contract data"))
    #expect(presentation.symbolName == "exclamationmark.triangle")
    #expect(!presentation.isActionable)
}
