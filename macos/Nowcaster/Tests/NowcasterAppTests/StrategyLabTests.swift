import AppKit
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
    let originalDatasetHash = try #require(duplicate["dataset_hash"] as? String)
    let originalStrategyID = try #require(duplicate["strategy_id"] as? String)
    let originalVersion = try #require(duplicate["version"] as? String)
    let originalSymbol = try #require(duplicate["symbol"] as? String)
    let originalInterval = try #require(duplicate["interval"] as? String)
    let originalMode = try #require(duplicate["mode"] as? String)
    duplicate["dataset_hash"] = String(repeating: "e", count: 64)
    duplicate["mode"] = "frozen"
    duplicate["cohort_id"] = "cohort-frozen-e"
    duplicate["weight"] = 0.8
    duplicate["causal_audit_passed"] = false
    duplicate["no_repaint_badge"] = "failed"
    strategies.append(duplicate)
    root["strategies"] = strategies

    var components = try #require(root["ensemble_components"] as? [[String: Any]])
    let componentIndex = try #require(components.firstIndex {
        $0["strategy_id"] as? String == originalStrategyID
            && $0["version"] as? String == originalVersion
            && $0["dataset_hash"] as? String == originalDatasetHash
            && $0["symbol"] as? String == originalSymbol
            && $0["interval"] as? String == originalInterval
            && $0["mode"] as? String == originalMode
    })
    components[componentIndex]["contribution"] = 0.18
    var component = components[componentIndex]
    component["dataset_hash"] = String(repeating: "e", count: 64)
    component["mode"] = "frozen"
    component["cohort_id"] = "cohort-frozen-e"
    component["contribution"] = -0.75
    component["effective_at"] = "2026-08-22T19:00:00Z"
    components.append(component)
    root["ensemble_components"] = components

    var coverage = try #require(root["dataset_coverage"] as? [[String: Any]])
    var contextCoverage = try #require(coverage.first {
        $0["dataset_hash"] as? String == originalDatasetHash
            && $0["symbol"] as? String == originalSymbol
            && $0["interval"] as? String == originalInterval
    })
    contextCoverage["dataset_hash"] = String(repeating: "e", count: 64)
    contextCoverage["provider"] = "csv"
    contextCoverage["feed"] = "local"
    coverage.append(contextCoverage)
    root["dataset_coverage"] = coverage

    var audits = try #require(root["causal_audits"] as? [[String: Any]])
    var audit = try #require(audits.first {
        $0["strategy_id"] as? String == originalStrategyID
            && $0["version"] as? String == originalVersion
            && $0["dataset_hash"] as? String == originalDatasetHash
            && $0["symbol"] as? String == originalSymbol
            && $0["interval"] as? String == originalInterval
            && $0["mode"] as? String == originalMode
    })
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
    let first = try #require(snapshot.strategies.first)
    let ids = Set(snapshot.strategies.filter {
        $0.datasetHash == first.datasetHash && $0.symbol == first.symbol
            && $0.interval == first.interval && $0.mode == first.mode
            && $0.cohortId == first.cohortId
    }.map(\.id))

    #expect(model.selectedStrategyIDs.count == 1)
    model.selectStrategies(ids)
    #expect(model.selectedStrategies.map(\.id) == snapshot.strategies.filter { ids.contains($0.id) }.map(\.id))
    #expect(model.selectedStrategy?.id == snapshot.strategies.first?.id)
    #expect(model.selectedResearchContext != nil)
    #expect(model.strategySelectionIssue == nil)
    #expect(model.strategyActionStatusIssue == "Select exactly one strategy for bounded learning.")
}

@Test @MainActor func duplicateStrategyContextsHaveStableIDsAndExactEvidenceJoins() throws {
    let snapshot = try strategyLabFixtureWithDuplicateContext()
    let lab = StrategyLabPresentation(snapshot: snapshot)
    let original = try #require(snapshot.strategies.first)
    let duplicateRows = lab.strategies.filter {
        $0.strategy.strategyId == original.strategyId
            && ($0.strategy.datasetHash == original.datasetHash
                || $0.strategy.datasetHash == String(repeating: "e", count: 64))
    }
    #expect(duplicateRows.count == 2)
    #expect(Set(duplicateRows.map(\.id)).count == duplicateRows.count)

    let paper = try #require(duplicateRows.first { $0.strategy.mode == "paper" })
    #expect(paper.component?.contribution == 0.18)
    #expect(paper.coverage?.provider == "csv")
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
    let snapshot = try strategyLabFixtureWithDuplicateContext()
    let lab = StrategyLabPresentation(snapshot: snapshot)
    let long = try #require(lab.strategies.first { $0.component?.contribution == 0.18 })
    let short = try #require(lab.strategies.first { ($0.component?.contribution ?? 0) < 0 })

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

    #expect(row.weightTitle == row.strategy.weight.formatted(.percent.precision(.fractionLength(1))))
    #expect(row.progressTitle == row.strategy.progress.formatted(.percent.precision(.fractionLength(0))))
    #expect(row.developmentSharpe == ResearchFormatting.metric(row.strategy.developmentMetrics["sharpe"] ?? nil))
    #expect(row.finalSharpe == ResearchFormatting.metric(row.strategy.finalTestMetrics["sharpe"] ?? nil))
    #expect(row.promotionTitle == "Rejected")
    #expect(row.noRepaintTitle == "No-repaint audit passed")
    #expect(row.evidenceGateTitle.contains("Causal audit passed"))
    #expect(row.warnings.contains("Historical evidence is not live proof"))
    #expect(row.uncertaintyDisclosure.localizedCaseInsensitiveContains("profit is not promised"))
    let coverage = try #require(row.coverage)
    #expect(row.coverageTitle.contains("\(coverage.provider)/\(coverage.feed)"))
    #expect(row.coverageTitle.contains("\(coverage.calendarId) \(coverage.calendarVersion)"))
}

@Test func failedAndUnauditedStatesUseTextNotColorAlone() throws {
    let snapshot = try strategyLabFixture()
    let strategy = try #require(snapshot.strategies.first)
    let failed = StrategyPresentation(strategy: strategy, component: nil, coverage: nil, audit: nil)

    #expect(failed.noRepaintTitle == "No-repaint audit passed")
    #expect(failed.causalAuditTitle == "Causal audit passed")
    #expect(failed.coverageTitle == "Coverage provenance unavailable")
    #expect(failed.statusAccessibilityLabel.contains("No-repaint audit passed"))
    #expect(failed.statusAccessibilityLabel.contains(failed.promotionTitle))

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
    #expect(empty.strategyEmptyDescription.contains("schema v5"))
    #expect(empty.learningEmptyTitle == "No learning runs")
    #expect(empty.learningEmptyDescription.contains("bounded learning"))
}

@Test func learningProgressExplainsBudgetRuleBoundaryAndAudit() throws {
    let run = try syntheticLearningRunFixture()
    let presentation = LearningRunPresentation(run: run)

    #expect(presentation.progressValue == run.progress)
    #expect(presentation.progressTitle == "\(run.evaluatedCandidates) of \(run.evaluationBudget) candidates")
    #expect(presentation.generationTitle == "Generation \(run.generation)")
    #expect(presentation.bestRule == run.bestRule)
    #expect(presentation.complexityTitle == "Complexity \(run.bestRuleDetail?.complexity ?? 0)")
    #expect(presentation.boundaryTitle.contains("Final boundary"))
    #expect(presentation.noRepaintTitle == "No-repaint audit unavailable")
    #expect(presentation.promotionTitle == "Shadow")
    #expect(presentation.accessibilityLabel.contains(presentation.progressTitle))
}

@Test func strategyLabAccessibilityContractsAreStableAndDescriptive() {
    #expect(StrategyLabAccessibility.table == "strategyLab.table")
    #expect(StrategyLabAccessibility.detail == "strategyLab.detail")
    #expect(StrategyLabAccessibility.learning == "strategyLab.learning")
    #expect(StrategyLabAccessibility.evaluateButton == "strategyLab.evaluate")
    #expect(StrategyLabAccessibility.learnButton == "strategyLab.learn")
    #expect(StrategyLabAccessibility.deepResearchButton == "strategyLab.deepResearch")
    #expect(StrategyLabAccessibility.deepResearchStartButton == "strategyLab.deepResearch.start")
    #expect(StrategyLabAccessibility.deepResearchWorkspace == "strategyLab.deepResearch.workspace")
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

@Test @MainActor func screenshotPresentationResizesAnExplicitRestoredWindow() {
    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 1_440, height: 900),
        styleMask: [.titled, .resizable],
        backing: .buffered,
        defer: false
    )
    let narrow = NowcasterWindowPresentation(arguments: ["--ui-narrow"])

    narrow.apply(to: window)

    #expect(abs(window.contentLayoutRect.width - 900) <= 1)
    #expect(abs(window.contentLayoutRect.height - 700) <= 1)
}

@Test func narrowNavigationAndBudgetControlsReserveReadableSpace() {
    #expect(RootSidebarPresentation.sectionHeaderLeadingPadding >= 20)
    #expect(StrategyLabLayout.budgetPickerWidth >= 100)
    #expect(StrategyLabLayout.budgetOptionTitle(20) == "20")
}

@Test func thermalPolicyPausesAtSeriousPressureAndOnlyAutoResumesItsOwnPause() {
    #expect(DeepResearchThermalPolicy.action(for: .nominal, automaticallyPaused: false) == .none)
    #expect(DeepResearchThermalPolicy.action(for: .fair, automaticallyPaused: true) == .resume)
    #expect(DeepResearchThermalPolicy.action(for: .serious, automaticallyPaused: false) == .pause)
    #expect(DeepResearchThermalPolicy.action(for: .critical, automaticallyPaused: false) == .pause)
}

@Test func deepResearchAlwaysReservesProcessorsForLiveMonitoringAndTheSystem() {
    #expect(DeepResearchResourcePolicy.maximumWorkerCount(activeProcessors: 16, reservedProcessors: 2) == 14)
    #expect(DeepResearchResourcePolicy.maximumWorkerCount(activeProcessors: 2, reservedProcessors: 2) == 1)
}

@Test func deepResearchPresentationMakesFailedGatesAndHypotheticalStatusExplicit() throws {
    let data = Data(
        """
        {"run_id":"deep-1","state":"completed","symbol":"BTCUSDT","interval":"5m",
        "provider":"binance","feed":"spot","dataset_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "protocol_id":"pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp",
        "started_at":"2026-08-25T00:00:00Z","updated_at":"2026-08-25T01:00:00Z",
        "final_test_start":"2026-08-26T00:00:00Z","continuous":false,"trial_budget":10,"cycle_budget":10,
        "evaluated_attempts":3,"succeeded_attempts":2,"failed_attempts":1,"generation":1,"progress":0.3,
        "best_candidate_hash":null,"champion_score":0.5,"outcome":"no_reliable_strategy_found",
        "failed_gates":["minimum 300 closed trades not met"],
        "resources":{"active_workers":4,"queued_trials":7,"memory_bytes":1000000,"thermal_state":"nominal"}}
        """.utf8
    )
    let run = try JSONDecoder.nowcaster.decode(DeepResearchRunSnapshot.self, from: data)
    let presentation = DeepResearchRunPresentation(run: run)

    #expect(presentation.outcomeTitle == "No Reliable Strategy Found")
    #expect(presentation.attemptsTitle == "3 of 10 attempts")
    #expect(presentation.failedGates == ["minimum 300 closed trades not met"])
    #expect(presentation.disclosure.localizedCaseInsensitiveContains("hypothetical"))
    #expect(presentation.disclosure.localizedCaseInsensitiveContains("not a trade instruction"))
}
