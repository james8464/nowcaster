import Testing
import Foundation

@testable import NowcasterApp

@Test func everyChartHasSummaryAndTableAlternative() {
    for chart in ChartAccessibility.fixtureCharts {
        #expect(!chart.summary.isEmpty)
        #expect(!chart.rows.isEmpty)
    }
}

@Test func directionDescriptionsDoNotDependOnColor() {
    #expect(ResearchPosture.longResearch.accessibilityDescription.contains("Long research"))
    #expect(ResearchPosture.shortResearch.accessibilityDescription.contains("Short research"))
    #expect(ResearchPosture.abstain.accessibilityDescription.contains("Abstain"))
}

@Test func accuracyEvidenceHasAPlainLanguageAccessibilitySummary() throws {
    let payload = Data(
        """
        {"signal_id":"rich","instrument_id":"BTCUSDT","asset_class":"crypto",\
        "decision_date":"2026-08-28","horizon":"5m","posture":"long_research",\
        "eligibility":"research_only","strength":null,"calibrated_probability":0.67,\
        "confidence_score":67,"catalyst":"bar","invalidation":"gate",\
        "evidence_summary":"calibrated","reasons":[],"probability_lower_bound":0.61,\
        "probability_upper_bound":0.73,"lower_net_edge":0.0011,"drift_status":"stable"}
        """.utf8
    )
    let signal = try JSONDecoder.nowcaster.decode(ResearchSignalSnapshot.self, from: payload)
    let summary = ResearchFormatting.evidenceAccessibilitySummary(signal)
    #expect(summary.contains("calibrated probability 67 percent"))
    #expect(summary.contains("range 61 to 73 percent"))
    #expect(summary.contains("lower cost-adjusted edge"))
    #expect(summary.contains("drift stable"))
    #expect(!summary.lowercased().contains("guarantee"))
}
