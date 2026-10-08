import Foundation
import Testing
@testable import NowcasterApp

@Test func intradayStatusDecodesWithoutClaimingReadiness() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-06T08:00:00Z","feed_health":"not_configured","evidence_status":"not_supported","markets":[{"market":"germany40","broker_symbol":null,"product":null,"eligibility":"unverified","reason":null}],"opportunities":[],"paper_positions":[],"no_trade_reason":"No demo feed. Stand aside."}
    """.utf8)
    let status = try IntradayDeskStatus.decode(data)
    #expect(status.feedHealth == "not_configured")
    #expect(status.opportunities.isEmpty)
    #expect(status.noTradeReason.contains("Stand aside"))
    #expect(!status.isFresh(at: Date(timeIntervalSince1970: 1_900_000_000)))
}

@Test func intradayStatusRejectsUnhealthyOrMalformedPaperOpportunity() throws {
    let template = """
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-07T08:00:00Z","feed_health":"%@","evidence_status":"not_supported","markets":[{"market":"germany40","broker_symbol":"DE30_EUR","product":"cfd","eligibility":"diagnostic","reason":null}],"opportunities":[{"market":"germany40","broker_symbol":"DE30_EUR","strategy_id":"opening_range_15","direction":"long","decided_at":"2026-10-07T08:15:00Z","entry_at":"2026-10-07T08:15:01Z","entry":"%@","stop":"100","target":"110","exit_by":"2026-10-07T09:00:00Z","estimated_roundtrip_cost":"2","explanation":"Paper only","paper_only":true}],"paper_positions":[],"no_trade_reason":""}
    """
    #expect(throws: Error.self) { try IntradayDeskStatus.decode(Data(String(format: template, "stale", "105").utf8)) }
    #expect(throws: Error.self) { try IntradayDeskStatus.decode(Data(String(format: template, "healthy", "95").utf8)) }
}

@Test func intradayStatusShowsPerProductFeedAgeWithoutUpgradingEvidence() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-08T09:00:02Z","feed_health":"healthy",
     "evidence_status":"not_supported","markets":[{"market":"germany40","broker_symbol":"DE30_EUR",
     "product":"cfd","eligibility":"diagnostic","reason":"costs unverified",
     "last_quote_at":"2026-10-08T09:00:00Z","feed_age_seconds":"2"}],
     "opportunities":[],"paper_positions":[],"no_trade_reason":"No confirmed setup."}
    """.utf8)
    let status = try IntradayDeskStatus.decode(data)
    #expect(Decimal(string: status.markets[0].feedAgeSeconds ?? "") == Decimal(2))
    #expect(status.markets[0].eligibility == "diagnostic")
    #expect(status.evidenceStatus == "not_supported")
}
