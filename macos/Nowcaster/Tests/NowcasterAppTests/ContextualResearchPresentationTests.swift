import Foundation
import Testing

@testable import NowcasterApp

private func contextualInstrument(_ fields: [String: Any] = [:]) throws -> InstrumentSnapshot {
    var record: [String: Any] = [
        "instrument_id": "BTCUSDT", "symbol": "BTCUSDT", "display_name": "Bitcoin",
        "asset_class": "crypto", "trend_regime": "insufficient", "price_history": [],
    ]
    record.merge(fields) { _, value in value }
    return try JSONDecoder.nowcaster.decode(InstrumentSnapshot.self, from: JSONSerialization.data(withJSONObject: record))
}

private var contextualFields: [String: Any] {
    [
        "context_hash": String(repeating: "a", count: 64),
        "contextual_evidence_hash": String(repeating: "b", count: 64),
        "contextual_effective_at": "2026-08-30T12:00:00Z",
        "asset_profile": "crypto_major_spot", "eligibility_state": "watch",
        "eligibility_reasons": ["spread_limit"], "eligibility_quality": 0.7,
        "regime_probabilities": ["trend_normal": 0.5, "trend_elevated_volatility": 0.1,
                                 "range_liquid": 0.3, "stressed_or_illiquid": 0.1],
        "portfolio_selected": false, "research_size_ceiling": 0.0,
        "portfolio_conflicts": ["correlation_cap"], "local_weight": 0.2, "parent_weight": 0.8,
        "final_weight": 0.1, "effective_observations": 18.0,
    ]
}

@Test func legacyContextualEvidenceIsOptionalAndNeverEligible() throws {
    let instrument = try contextualInstrument()
    try instrument.validateContextualEvidence()
    #expect(instrument.eligibilityState == nil)
    let presentation = ContextualResearchPresentation(evidence: instrument)
    #expect(presentation.eligibilityTitle == "Not assessed")
    #expect(presentation.portfolioTitle == "No authenticated selection")
}

@Test func contextualEvidenceExplainsRegimesReasonsAndParentInfluence() throws {
    let instrument = try contextualInstrument(contextualFields)
    try instrument.validateContextualEvidence()
    let now = try #require(ISO8601DateFormatter().date(from: "2026-08-30T12:05:00Z"))
    let presentation = ContextualResearchPresentation(evidence: instrument, now: now)
    #expect(presentation.eligibilityTitle == "Watch only")
    #expect(presentation.regimeTitle == "Normal trend")
    #expect(presentation.regimes.count == 4)
    #expect(presentation.reasons.contains("The bid–ask spread is too wide."))
    #expect(presentation.portfolioTitle == "Not selected")
    #expect(presentation.influenceTitle.contains("20%"))
    #expect(presentation.sizeDisclaimer.contains("not an order"))
}

@Test func contextualEvidencePreservesUnknownStatesWithoutGrantingEligibility() throws {
    var fields = contextualFields
    fields["eligibility_state"] = "future_state"
    let instrument = try contextualInstrument(fields)
    #expect(instrument.eligibilityState == .unknown("future_state"))
    #expect(ContextualResearchPresentation(evidence: instrument).eligibilityTitle != "Eligible for research")
}

@Test func contextualEvidenceRejectsMalformedProbabilityAndWeightBounds() throws {
    for field: [String: Any] in [
        ["regime_probabilities": ["trend_normal": 1.1]],
        ["local_weight": 0.9],
        ["research_size_ceiling": -0.1],
        ["eligibility_reasons": Array(repeating: "reason", count: 65)],
        ["context_hash": "not-a-hash"],
        ["portfolio_selected": true],
    ] {
        var fields = contextualFields
        fields.merge(field) { _, value in value }
        let instrument = try contextualInstrument(fields)
        #expect(throws: SnapshotValidationError.self) { try instrument.validateContextualEvidence() }
    }
}

@Test func contextualHistoricalEvidenceIsExplicitlyOutOfDate() throws {
    let instrument = try contextualInstrument(contextualFields)
    let now = try #require(ISO8601DateFormatter().date(from: "2026-09-01T12:00:00Z"))
    let presentation = ContextualResearchPresentation(evidence: instrument, now: now)
    #expect(presentation.isStale)
    #expect(presentation.eligibilityTitle == "Out of date")
}
