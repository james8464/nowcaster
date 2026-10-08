import Foundation
import Testing

@testable import NowcasterApp

@Test func paperReportShowsNegativeNetDespitePositiveGross() throws {
    let data = Data("""
    {"schema_version":1,"paper_only":true,"round_id":"round-2","generated_at":"2026-10-08T09:00:00Z",
     "evidence_status":"insufficient_evidence","coverage_status":"not_measured",
     "closed_trades":4,"open_positions":0,"no_trade_count":3,
     "gross_pnl_gbp":"1","commission_gbp":"4","financing_gbp":"4","net_pnl_gbp":"-7",
     "win_rate":"0.75","net_expectancy_gbp":"-1.75","daily_block_lower_95_gbp":null,
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
     "closed_trades":0,"open_positions":1,"no_trade_count":0,
     "gross_pnl_gbp":"0","commission_gbp":"0","financing_gbp":"0","net_pnl_gbp":"0",
     "win_rate":null,"net_expectancy_gbp":null,"daily_block_lower_95_gbp":null,
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
