import Testing

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
