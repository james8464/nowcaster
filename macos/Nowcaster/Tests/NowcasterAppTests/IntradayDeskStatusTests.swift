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

@Test func diagnosticSetupCannotBePresentedAsAnOpenedPaperTrade() throws {
    let diagnostic = """
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-08T09:00:02Z",
     "feed_health":"healthy","evidence_status":"not_supported",
     "markets":[{"market":"us500","broker_symbol":"SPX500_USD","product":"cfd","eligibility":"diagnostic"}],
     "opportunities":[{"market":"us500","broker_symbol":"SPX500_USD","strategy_id":"trend_pullback",
       "direction":"short","decided_at":"2026-10-08T09:00:00Z","entry_at":"2026-10-08T09:00:01Z",
       "entry":"5000","stop":"5010","target":"4980","exit_by":"2026-10-08T10:00:00Z",
       "estimated_roundtrip_cost":"0.5","explanation":"Diagnostic setup","paper_only":true}],
     "paper_positions":[],"no_trade_reason":""}
    """
    let watchOnly = try IntradayDeskStatus.decode(Data(diagnostic.utf8))
    #expect(watchOnly.opportunityStateLabel(watchOnly.opportunities[0]) == "Diagnostic setup — not a paper trade")

    let opened = diagnostic.replacingOccurrences(of: "\"paper_positions\":[]", with: """
    "paper_positions":[{"market":"us500","broker_symbol":"SPX500_USD","direction":"short",
      "entry":"4999.5","stop":"5010","target":"4980","opened_at":"2026-10-08T09:00:04Z",
      "exit_by":"2026-10-08T10:00:00Z","paper_only":true}]
    """)
    let withPosition = try IntradayDeskStatus.decode(Data(opened.utf8))
    #expect(withPosition.opportunityStateLabel(withPosition.opportunities[0]) == "Diagnostic setup — not a paper trade")
    #expect(withPosition.paperPositions.count == 1)
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

@Test func intradayMarketQuoteAgeAdvancesAndRetainsBrokerDisplayName() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-08T09:00:02Z",
     "feed_health":"healthy","evidence_status":"not_supported",
     "markets":[{"market":"germany40","broker_symbol":"DE30_EUR","display_name":"Germany 30",
       "product":"cfd","eligibility":"diagnostic","last_quote_at":"2026-10-08T09:00:00Z","feed_age_seconds":"2"}],
     "opportunities":[],"paper_positions":[],"no_trade_reason":"No trade"}
    """.utf8)
    let status = try IntradayDeskStatus.decode(data)
    let at = ISO8601DateFormatter().date(from: "2026-10-08T09:02:00Z")!
    #expect(status.markets[0].displayName == "Germany 30")
    #expect(status.markets[0].quoteAgeLabel(at: at).contains("stale"))
}

@Test func accountCaptureQualityDisplaysCoverageWithoutPaperEligibility() throws {
    let data = Data("""
    {"generated_at":"2026-10-08T09:06:00Z","round_id":"diagnostic-2026-10-08",
     "price_scope":"account_stream_observation","paper_eligible":false,
     "markets":[{"broker_symbol":"DE30_EUR","observed_from":"2026-10-08T07:00:00Z",
       "observed_until":"2026-10-08T09:06:00Z","expected_intervals":25,
       "covered_intervals":1,"quote_count":2,"tradeable_quote_count":2,
       "invalid_quote_count":0,"coverage":"0.04","median_spread":"2",
       "p95_spread":"2","max_quote_gap_seconds":"300",
       "price_scope":"account_stream_observation","paper_eligible":false}]}
    """.utf8)
    let quality = try IntradayCaptureQuality.decode(data)
    #expect(quality.markets[0].coverageLabel == "1/25 observed intervals (4%)")
    #expect(quality.markets[0].spreadLabel == "Median spread 2 · 95th percentile 2")
    #expect(!quality.paperEligible)
    #expect(!quality.isFresh(at: Date(timeIntervalSince1970: 1_900_000_000)))
}
