import Foundation
import Testing

@testable import NowcasterApp

@Test func paperReportShowsNegativeNetDespitePositiveGross() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"round_id":"round-2","generated_at":"2026-10-08T09:00:00Z",
     "evidence_status":"insufficient_evidence","coverage_status":"not_measured",
     "decisions_count":7,"blocked_reasons":{"no_setup":3},"feed_gap_count":1,
     "closed_trades":4,"open_positions":0,"no_trade_count":3,
     "gross_pnl_gbp":"1","commission_gbp":"4","financing_gbp":"4","net_pnl_gbp":"-7",
     "win_rate":"0.75","net_expectancy_gbp":"-1.75","daily_block_lower_95_gbp":null,
     "profit_factor":"0.6","maximum_drawdown_gbp":"10",
     "groups":[{"broker_symbol":"DE30_EUR","strategy_id":"trend_pullback","direction":"long","session_date":"2026-10-08","closed_trades":4,"net_pnl_gbp":"-7","win_rate":"0.75"}],
     "trades":[
       {"open":{"broker_symbol":"DE30_EUR","direction":"long","opened_at":"2026-10-08T09:00:00Z"},"close":{"exit_reason":"target","exit_price":"100","net_pnl_gbp":"1"}},
       {"open":{"broker_symbol":"DE30_EUR","direction":"long","opened_at":"2026-10-08T09:10:00Z"},"close":{"exit_reason":"target","exit_price":"100","net_pnl_gbp":"1"}},
       {"open":{"broker_symbol":"DE30_EUR","direction":"long","opened_at":"2026-10-08T09:20:00Z"},"close":{"exit_reason":"target","exit_price":"100","net_pnl_gbp":"1"}},
       {"open":{"broker_symbol":"DE30_EUR","direction":"long","opened_at":"2026-10-08T09:30:00Z"},"close":{"exit_reason":"stop","exit_price":"90","net_pnl_gbp":"-10"}}],
     "warning":"Paper fills are hypothetical."}
    """.utf8)
    let report = try IntradayPaperReport.decode(data)
    #expect(report.netPnL == Decimal(-7))
    #expect(report.winRate == Decimal(string: "0.75"))
    #expect(report.evidenceStatus == "insufficient_evidence")
    #expect(report.dailyLowerBound == nil)
}

@Test func paperReportRetainsCompleteOpenTicket() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"round_id":"round-2","generated_at":"2026-10-08T09:00:00Z",
     "evidence_status":"insufficient_evidence","coverage_status":"not_measured",
     "decisions_count":1,"blocked_reasons":{},"feed_gap_count":0,
     "closed_trades":0,"open_positions":1,"no_trade_count":0,
     "gross_pnl_gbp":"0","commission_gbp":"0","financing_gbp":"0","net_pnl_gbp":"0",
     "win_rate":null,"net_expectancy_gbp":null,"daily_block_lower_95_gbp":null,
     "profit_factor":null,"maximum_drawdown_gbp":"0","groups":[],"trades":[],
     "unresolved_positions":[{"broker_symbol":"DE30_EUR","direction":"long","entry":"24001","stop":"23950",
     "target":"24103","units":"1","notional_gbp":"20000","effective_leverage":"0.5",
     "margin_estimate_gbp":"1000","estimated_roundtrip_cost_gbp":"5","exit_by":"2026-10-08T10:00:00Z",
     "cost_source":"https://broker.example/terms"}],"warning":"Paper fills are hypothetical."}
    """.utf8)
    let report = try IntradayPaperReport.decode(data)
    #expect(report.tickets.count == 1)
    #expect(report.tickets[0].stop == Decimal(23950))
    #expect(report.tickets[0].effectiveLeverage == Decimal(string: "0.5"))
    #expect(report.tickets[0].costSource.hasPrefix("https://"))
}

@Test func paperReportRetainsEvidenceAndClosedTradeBreakdown() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"round_id":"round-2","generated_at":"2026-10-08T10:00:00Z",
     "evidence_status":"insufficient_evidence","coverage_status":"measured","account_quote_coverage":"0.99",
     "decisions_count":5,"blocked_reasons":{"session_closed":2},"feed_gap_count":1,
     "closed_trades":1,"open_positions":0,"no_trade_count":2,
     "gross_pnl_gbp":"-4","commission_gbp":"1","financing_gbp":"1","net_pnl_gbp":"-6",
     "win_rate":"0","net_expectancy_gbp":"-6","daily_block_lower_95_gbp":null,
     "profit_factor":"0","maximum_drawdown_gbp":"6",
     "groups":[{"broker_symbol":"DE30_EUR","strategy_id":"trend_pullback","direction":"long",
       "session_date":"2026-10-08","closed_trades":1,"net_pnl_gbp":"-6","win_rate":"0"}],
     "trades":[{"open":{"broker_symbol":"DE30_EUR","direction":"long","entry":"24001","opened_at":"2026-10-08T09:00:00Z"},
       "close":{"broker_symbol":"DE30_EUR","exit_reason":"stop","exit_price":"23997","net_pnl_gbp":"-6"}}],
     "unresolved_positions":[],"warning":"Paper fills are hypothetical."}
    """.utf8)
    let report = try IntradayPaperReport.decode(data)
    #expect(report.decisionsCount == 5)
    #expect(report.feedGapCount == 1)
    #expect(report.blockedReasons["session_closed"] == 2)
    #expect(report.profitFactor == 0)
    #expect(report.maximumDrawdown == 6)
    #expect(report.groups[0].brokerSymbol == "DE30_EUR")
    #expect(report.closedRecords[0].exitReason == "stop")
}
