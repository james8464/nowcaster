import Foundation
import Testing

@testable import NowcasterApp

private func strategyLabFixture() throws -> NowcasterSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    return try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
}

private func strategyLabFixtureWithDuplicateContext() throws -> NowcasterSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    var root = try #require(
        JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
    )
    var strategies = try #require(root["strategies"] as? [[String: Any]])
    var duplicate = try #require(strategies.first)
    duplicate["dataset_hash"] = String(repeating: "e", count: 64)
    duplicate["mode"] = "frozen"
    duplicate["cohort_id"] = "cohort-frozen-e"
    duplicate["weight"] = 0.8
    duplicate["causal_audit_passed"] = false
    duplicate["no_repaint_badge"] = "failed"
    strategies.append(duplicate)
    root["strategies"] = strategies

    var components = try #require(root["ensemble_components"] as? [[String: Any]])
    var component = try #require(components.first)
    component["dataset_hash"] = String(repeating: "e", count: 64)
    component["mode"] = "frozen"
    component["cohort_id"] = "cohort-frozen-e"
    component["contribution"] = -0.75
    component["effective_at"] = "2026-08-22T19:00:00Z"
    components.append(component)
    root["ensemble_components"] = components

    var coverage = try #require(root["dataset_coverage"] as? [[String: Any]])
    var contextCoverage = try #require(coverage.first)
    contextCoverage["dataset_hash"] = String(repeating: "e", count: 64)
    contextCoverage["provider"] = "csv"
    contextCoverage["feed"] = "local"
    coverage.append(contextCoverage)
    root["dataset_coverage"] = coverage

    var audits = try #require(root["causal_audits"] as? [[String: Any]])
    var audit = try #require(audits.first)
    audit["audit_id"] = "audit-frozen-e"
    audit["dataset_hash"] = String(repeating: "e", count: 64)
    audit["mode"] = "frozen"
    audit["passed"] = false
    audit["no_repaint_badge"] = "failed"
    audits.append(audit)
    root["causal_audits"] = audits

    return try JSONDecoder.nowcaster.decode(
        NowcasterSnapshot.self,
        from: JSONSerialization.data(withJSONObject: root)
    )
}

@Test func strategyLabIsAStableResearchDestination() {
    #expect(AppDestination.strategyLab.title == "Strategy Lab")
    #expect(AppDestination.strategyLab.symbolName == "point.3.connected.trianglepath.dotted")
    #expect(AppDestination.allCases.contains(.strategyLab))
}

@Test @MainActor func strategySelectionSupportsPluralEvaluationAndOneInspector() throws {
    let snapshot = try strategyLabFixture()
    let model = AppModel(snapshot: snapshot)
    let ids = Set(snapshot.strategies.map(\.id))

    #expect(model.selectedStrategyIDs.count == 1)
    model.selectStrategies(ids)
    #expect(model.selectedStrategies.map(\.id) == snapshot.strategies.map(\.id))
    #expect(model.selectedStrategy?.id == snapshot.strategies.first?.id)
    #expect(model.selectedResearchContext != nil)
    #expect(model.strategySelectionIssue == nil)
    #expect(model.strategyActionStatusIssue == "Select exactly one strategy for bounded learning.")
}

@Test @MainActor func duplicateStrategyContextsHaveStableIDsAndExactEvidenceJoins() throws {
    let snapshot = try strategyLabFixtureWithDuplicateContext()
    let lab = StrategyLabPresentation(snapshot: snapshot)
    let duplicateRows = lab.strategies.filter { $0.strategy.strategyId == "rsi_reversal" }
    #expect(duplicateRows.count == 2)
    #expect(Set(duplicateRows.map(\.id)).count == duplicateRows.count)

    let paper = try #require(duplicateRows.first { $0.strategy.mode == "paper" })
    #expect(paper.component?.contribution == 0.18)
    #expect(paper.coverage?.provider == "binance")
    #expect(paper.audit?.passed == true)

    let frozen = try #require(duplicateRows.first { $0.strategy.mode == "frozen" })
    #expect(frozen.component?.contribution == -0.75)
    #expect(frozen.coverage?.provider == "csv")
    #expect(frozen.audit?.passed == false)

    let model = AppModel(snapshot: snapshot)
    model.selectStrategies(Set(duplicateRows.map(\.id)))
    #expect(model.selectedResearchContext == nil)
    #expect(model.strategySelectionIssue?.contains("same dataset, source, asset, interval, mode, and cohort") == true)
}

@Test func signedContributionProducesExplicitResearchPostureText() throws {
    let snapshot = try strategyLabFixture()
    let lab = StrategyLabPresentation(snapshot: snapshot)
    let long = try #require(lab.strategies.first { $0.component?.contribution == 0.18 })
    let short = try #require(lab.strategies.first { $0.component?.contribution == -0.06 })

    #expect(long.posture == .longResearch)
    #expect(long.postureTitle == "Long research")
    #expect(short.posture == .shortResearch)
    #expect(short.postureTitle == "Short research")
    #expect(long.directionAccessibilityLabel.contains("positive current ensemble contribution"))
    #expect(short.directionAccessibilityLabel.contains("negative current ensemble contribution"))
    #expect(long.directionAccessibilityLabel.contains("not a trade instruction"))

    let abstain = StrategyPresentation(strategy: snapshot.strategies[0], component: nil, coverage: nil, audit: nil)
    #expect(abstain.posture == .abstain)
    #expect(abstain.postureTitle == "Abstain")
    #expect(abstain.directionAccessibilityLabel.contains("No signed current contribution"))
}

@Test func strategyPresentationSeparatesEvidenceAndDisclosesWarnings() throws {
    let snapshot = try strategyLabFixture()
    let row = try #require(StrategyLabPresentation(snapshot: snapshot).strategies.first)

    #expect(row.weightTitle == "35.0%")
    #expect(row.progressTitle == "100%")
    #expect(row.developmentSharpe == "1.25")
    #expect(row.finalSharpe == "0.48")
    #expect(row.promotionTitle == "Research Only")
    #expect(row.noRepaintTitle == "No-repaint audit passed")
    #expect(row.evidenceGateTitle.contains("Causal audit passed"))
    #expect(row.warnings.contains("Historical evidence is not live proof"))
    #expect(row.uncertaintyDisclosure.localizedCaseInsensitiveContains("profit is not promised"))
    #expect(row.coverageTitle.contains("binance/spot"))
    #expect(row.coverageTitle.contains("24/7"))
}

@Test func failedAndUnauditedStatesUseTextNotColorAlone() throws {
    let snapshot = try strategyLabFixture()
    let strategy = try #require(snapshot.strategies.first)
    let failed = StrategyPresentation(strategy: strategy, component: nil, coverage: nil, audit: nil)

    #expect(failed.noRepaintTitle == "No-repaint audit passed")
    #expect(failed.causalAuditTitle == "Causal audit passed")
    #expect(failed.coverageTitle == "Coverage provenance unavailable")
    #expect(failed.statusAccessibilityLabel.contains("No-repaint audit passed"))
    #expect(failed.statusAccessibilityLabel.contains("Research Only"))

    let failedData = Data(
        """
        {"strategy_id":"broken","version":"1","family":"trend",\
        "dataset_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",\
        "symbol":"BTCUSDT","interval":"5m","mode":"paper","cohort_id":"cohort-broken",
        "state":"failed","weight":0,"development_metrics":{},"final_test_metrics":{},"warnings":[],
        "generation":1,"progress":0,"complexity":null,"promotion_state":"rejected",
        "causal_audit_passed":false,"no_repaint_badge":"failed","latest_run_at":null}
        """.utf8
    )
    let failedStrategy = try JSONDecoder.nowcaster.decode(StrategySnapshot.self, from: failedData)
    let failedPresentation = StrategyPresentation(
        strategy: failedStrategy,
        component: nil,
        coverage: nil,
        audit: nil
    )
    #expect(failedPresentation.causalAuditTitle == "Causal audit failed")
    #expect(failedPresentation.noRepaintTitle == "No-repaint audit failed")
    #expect(failedPresentation.statusAccessibilityLabel.contains("Rejected"))
}

@Test func emptyStrategyAndLearningStatesAreActionable() {
    let empty = StrategyLabPresentation(
        strategies: [],
        components: [],
        coverage: [],
        audits: [],
        learningRuns: []
    )

    #expect(empty.strategyEmptyTitle == "Strategy evidence unavailable")
    #expect(empty.strategyEmptyDescription.contains("schema v2"))
    #expect(empty.learningEmptyTitle == "No learning runs")
    #expect(empty.learningEmptyDescription.contains("bounded learning"))
}

@Test func learningProgressExplainsBudgetRuleBoundaryAndAudit() throws {
    let run = try #require(try strategyLabFixture().learningRuns.first)
    let presentation = LearningRunPresentation(run: run)

    #expect(presentation.progressValue == 0.6)
    #expect(presentation.progressTitle == "12 of 20 candidates")
    #expect(presentation.generationTitle == "Generation 3")
    #expect(presentation.bestRule.contains("Prior-bar RSI"))
    #expect(presentation.complexityTitle == "Complexity 5")
    #expect(presentation.boundaryTitle.contains("Final boundary"))
    #expect(presentation.noRepaintTitle == "No-repaint audit passed")
    #expect(presentation.promotionTitle == "Shadow")
    #expect(presentation.accessibilityLabel.contains("12 of 20 candidates"))
}

@Test func strategyLabAccessibilityContractsAreStableAndDescriptive() {
    #expect(StrategyLabAccessibility.table == "strategyLab.table")
    #expect(StrategyLabAccessibility.detail == "strategyLab.detail")
    #expect(StrategyLabAccessibility.learning == "strategyLab.learning")
    #expect(StrategyLabAccessibility.evaluateButton == "strategyLab.evaluate")
    #expect(StrategyLabAccessibility.learnButton == "strategyLab.learn")
    #expect(StrategyLabAccessibility.exportButton == "strategyLab.export")
    #expect(StrategyLabAccessibility.directionLabel.contains("ensemble contribution"))
    #expect(StrategyLabAccessibility.progressLabel.contains("evaluation budget"))
}

@Test func screenshotWindowContractCreatesDistinctUsableWideAndNarrowLayouts() {
    let wide = NowcasterWindowPresentation(arguments: ["--ui-wide"])
    let narrow = NowcasterWindowPresentation(arguments: ["--ui-narrow"])

    #expect(wide.defaultWidth == 1_440)
    #expect(narrow.defaultWidth == 900)
    #expect(narrow.defaultHeight == 700)
    #expect(narrow.minimumWidth == 820)
    #expect(narrow.defaultWidth < wide.defaultWidth)
}

@Test func narrowNavigationAndBudgetControlsReserveReadableSpace() {
    #expect(RootSidebarPresentation.sectionHeaderLeadingPadding >= 20)
    #expect(StrategyLabLayout.budgetPickerWidth >= 100)
    #expect(StrategyLabLayout.budgetOptionTitle(20) == "20")
}
