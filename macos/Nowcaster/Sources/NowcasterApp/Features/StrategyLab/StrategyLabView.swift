import SwiftUI

enum StrategyResearchPosture: Sendable {
    case longResearch
    case shortResearch
    case abstain

    var title: String {
        switch self {
        case .longResearch: "Long research"
        case .shortResearch: "Short research"
        case .abstain: "Abstain"
        }
    }

    var symbolName: String {
        switch self {
        case .longResearch: "arrow.up.right"
        case .shortResearch: "arrow.down.right"
        case .abstain: "pause"
        }
    }

    var color: Color {
        switch self {
        case .longResearch: .green
        case .shortResearch: .red
        case .abstain: .secondary
        }
    }
}

enum StrategyLabAccessibility {
    static let table = "strategyLab.table"
    static let detail = "strategyLab.detail"
    static let learning = "strategyLab.learning"
    static let evaluateButton = "strategyLab.evaluate"
    static let learnButton = "strategyLab.learn"
    static let deepResearchButton = "strategyLab.deepResearch"
    static let deepResearchStartButton = "strategyLab.deepResearch.start"
    static let deepResearchWorkspace = "strategyLab.deepResearch.workspace"
    static let exportButton = "strategyLab.export"
    static let directionLabel = "Research posture from signed current ensemble contribution"
    static let progressLabel = "Learning progress against the fixed evaluation budget"
}

struct StrategyPresentation: Identifiable, Sendable {
    let strategy: StrategySnapshot
    let component: EnsembleComponentSnapshot?
    let coverage: DatasetCoverageSnapshot?
    let audit: CausalAuditSnapshot?

    var id: String { strategy.id }
    var posture: StrategyResearchPosture {
        guard let contribution = component?.contribution, contribution != 0 else { return .abstain }
        return contribution > 0 ? .longResearch : .shortResearch
    }
    var postureTitle: String { posture.title }
    var familyTitle: String { strategy.family.researchTitle }
    var weightTitle: String { strategy.weight.formatted(.percent.precision(.fractionLength(1))) }
    var progressTitle: String { strategy.progress.formatted(.percent.precision(.fractionLength(0))) }
    var contributionTitle: String {
        guard let contribution = component?.contribution else { return "Unavailable" }
        return contribution.formatted(.number.precision(.fractionLength(3)).sign(strategy: .always()))
    }
    var developmentSharpe: String { ResearchFormatting.metric(strategy.developmentMetrics["sharpe"] ?? nil) }
    var finalSharpe: String { ResearchFormatting.metric(strategy.finalTestMetrics["sharpe"] ?? nil) }
    var promotionTitle: String { strategy.promotionState.researchTitle }
    var causalAuditTitle: String {
        switch audit?.passed ?? strategy.causalAuditPassed {
        case true: "Causal audit passed"
        case false: "Causal audit failed"
        case nil: "Causal audit unavailable"
        }
    }
    var noRepaintTitle: String {
        switch audit?.noRepaintBadge ?? strategy.noRepaintBadge {
        case .passed: "No-repaint audit passed"
        case .failed: "No-repaint audit failed"
        case .notAudited: "No-repaint audit unavailable"
        }
    }
    var evidenceGateTitle: String { "\(promotionTitle) · \(causalAuditTitle)" }
    var coverageTitle: String {
        guard let coverage else { return "Coverage provenance unavailable" }
        return "\(coverage.provider)/\(coverage.feed) · \(coverage.rowCount.formatted()) bars · \(coverage.calendarId) \(coverage.calendarVersion)"
    }
    var warnings: [String] { strategy.warnings }
    var directionAccessibilityLabel: String {
        switch posture {
        case .longResearch:
            "Long research posture from positive current ensemble contribution; not a trade instruction."
        case .shortResearch:
            "Short research posture from negative current ensemble contribution; not a trade instruction."
        case .abstain:
            "Abstain. No signed current contribution clears a research posture; not a trade instruction."
        }
    }
    var statusAccessibilityLabel: String {
        "\(promotionTitle). \(causalAuditTitle). \(noRepaintTitle)."
    }
    var uncertaintyDisclosure: String {
        "Research evidence is uncertain, may not persist out of sample, and profit is not promised. This is not an order or trade instruction."
    }
}

struct LearningRunPresentation: Identifiable, Sendable {
    let run: LearningRunSnapshot

    var id: String { run.id }
    var progressValue: Double { run.progress }
    var progressTitle: String { "\(run.evaluatedCandidates) of \(run.evaluationBudget) candidates" }
    var generationTitle: String { "Generation \(run.generation)" }
    var bestRule: String { run.bestRule ?? "No rule discovered" }
    var complexityTitle: String {
        guard let complexity = run.bestRuleDetail?.complexity else { return "Complexity unavailable" }
        return "Complexity \(complexity)"
    }
    var boundaryTitle: String {
        "Final boundary \(run.finalBoundary.formatted(date: .abbreviated, time: .standard))"
    }
    var promotionTitle: String { run.promotionState.researchTitle }
    var noRepaintTitle: String {
        switch run.noRepaintBadge {
        case .passed: "No-repaint audit passed"
        case .failed: "No-repaint audit failed"
        case .notAudited: "No-repaint audit unavailable"
        }
    }
    var accessibilityLabel: String {
        "\(run.state.researchTitle), \(progressTitle), \(generationTitle), \(promotionTitle), \(noRepaintTitle)"
    }
}

struct StrategyLabPresentation: Sendable {
    let strategies: [StrategyPresentation]
    let learningRuns: [LearningRunPresentation]
    let deepResearchRuns: [DeepResearchRunPresentation]

    init(snapshot: NowcasterSnapshot) {
        self.init(
            strategies: snapshot.strategies,
            components: snapshot.ensembleComponents,
            coverage: snapshot.datasetCoverage,
            audits: snapshot.causalAudits,
            learningRuns: snapshot.learningRuns,
            deepResearchRuns: snapshot.deepResearchRuns ?? []
        )
    }

    init(
        strategies: [StrategySnapshot],
        components: [EnsembleComponentSnapshot],
        coverage: [DatasetCoverageSnapshot],
        audits: [CausalAuditSnapshot],
        learningRuns: [LearningRunSnapshot],
        deepResearchRuns: [DeepResearchRunSnapshot] = []
    ) {
        self.strategies = strategies.map { strategy in
            let matchingComponents = components.filter {
                $0.strategyId == strategy.strategyId && $0.version == strategy.version
                    && $0.datasetHash == strategy.datasetHash && $0.symbol == strategy.symbol
                    && $0.interval == strategy.interval && $0.mode == strategy.mode
                    && $0.cohortId == strategy.cohortId
            }
            let matchingCoverage = coverage
                .filter {
                    $0.datasetHash == strategy.datasetHash && $0.symbol == strategy.symbol
                        && $0.interval == strategy.interval
                }
                .max { $0.requestedEnd < $1.requestedEnd }
            let matchingAudit = audits
                .filter {
                    $0.strategyId == strategy.strategyId && $0.version == strategy.version
                        && $0.datasetHash == strategy.datasetHash && $0.symbol == strategy.symbol
                        && $0.interval == strategy.interval && $0.mode == strategy.mode
                }
                .max { $0.auditedAt < $1.auditedAt }
            return StrategyPresentation(
                strategy: strategy,
                component: matchingComponents.max { $0.effectiveAt < $1.effectiveAt },
                coverage: matchingCoverage,
                audit: matchingAudit
            )
        }
        self.learningRuns = learningRuns.map(LearningRunPresentation.init)
        self.deepResearchRuns = deepResearchRuns.map(DeepResearchRunPresentation.init)
    }

    var strategyEmptyTitle: String { "Strategy evidence unavailable" }
    var strategyEmptyDescription: String {
        "This schema v5 snapshot contains no compatible strategy evidence. Run a scoped evaluation, then export again."
    }
    var learningEmptyTitle: String { "No learning runs" }
    var learningEmptyDescription: String {
        "Start bounded learning after compatible causal coverage is available."
    }
}

extension String {
    var researchTitle: String {
        replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}

enum StrategyLabLayout {
    static let budgetPickerWidth: CGFloat = 112

    static func budgetOptionTitle(_ budget: Int) -> String { String(budget) }
}

struct StrategyJobStatusPresentation: Equatable, Sendable {
    let message: String
    let progressValue: Double?
    let isRunning: Bool
    let isFailure: Bool
    let symbolName: String

    init(
        isRunning: Bool,
        progress: ActiveJobProgress?,
        outcome: EngineJobOutcome,
        selectionIssue: String?
    ) {
        self.isRunning = isRunning
        progressValue = isRunning ? progress?.value : nil
        if isRunning {
            message = progress?.message ?? "Running research job…"
            isFailure = false
            symbolName = "gearshape.2"
        } else if case let .failure(_, failure) = outcome {
            message = failure
            isFailure = true
            symbolName = "exclamationmark.triangle"
        } else if let selectionIssue {
            message = selectionIssue
            isFailure = false
            symbolName = "info.circle"
        } else if case let .success(_, success) = outcome {
            message = success
            isFailure = false
            symbolName = "checkmark.circle"
        } else {
            message = "Research only · no broker connection"
            isFailure = false
            symbolName = "doc.text.magnifyingglass"
        }
    }

    var accessibilityLabel: String {
        isFailure ? "Research job failed. \(message)" : message
    }
}

struct StrategyLabView: View {
    @Bindable var model: AppModel
    let settings: AppSettings
    let snapshot: NowcasterSnapshot
    @State private var learningBudget = 20
    @State private var showingDeepResearchConfiguration = false

    private var presentation: StrategyLabPresentation { StrategyLabPresentation(snapshot: snapshot) }
    private var selectedResearchContext: SelectedStrategyResearchContext? { model.selectedResearchContext }

    var body: some View {
        VStack(spacing: 0) {
            actionBar
            Divider()
            if presentation.strategies.isEmpty {
                EmptyStateView(
                    title: presentation.strategyEmptyTitle,
                    systemImage: "chart.line.text.clipboard",
                    description: presentation.strategyEmptyDescription
                )
            } else {
                VSplitView {
                    strategyTable.frame(minHeight: 250)
                    TabView {
                        DeepResearchWorkspaceView(model: model, runs: presentation.deepResearchRuns)
                            .tabItem { Label("Deep Research", systemImage: "cpu") }
                        LearningWorkspaceView(
                            model: model,
                            runs: presentation.learningRuns,
                            emptyTitle: presentation.learningEmptyTitle,
                            emptyDescription: presentation.learningEmptyDescription
                        )
                        .tabItem { Label("Bounded Learning", systemImage: "wand.and.stars") }
                    }
                    .frame(minHeight: 250)
                }
            }
        }
        .sheet(isPresented: $showingDeepResearchConfiguration) {
            if let context = selectedResearchContext {
                DeepResearchConfigurationView(model: model, settings: settings, context: context)
            }
        }
    }

    private var actionBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                evaluateButton(compact: false)
                deepResearchButton(compact: false)
                learnButton(compact: false)
                budgetPicker
                exportButton(compact: false)
                Spacer()
                deepResearchControls
                jobStatus(compact: false)
            }
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    evaluateButton(compact: true)
                    deepResearchButton(compact: true)
                    learnButton(compact: true)
                    exportButton(compact: true)
                    budgetPicker
                    Spacer()
                }
                jobStatus(compact: true)
            }
        }
        .padding()
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 52)
    }

    private func deepResearchButton(compact: Bool) -> some View {
        Button {
            showingDeepResearchConfiguration = true
        } label: {
            actionLabel("Deep Research", systemImage: "cpu", compact: compact)
        }
        .disabled(model.isRunningJob || selectedResearchContext == nil)
        .accessibilityLabel("Configure and start Deep Research")
        .accessibilityIdentifier(StrategyLabAccessibility.deepResearchButton)
        .help(model.strategySelectionIssue ?? "Search and stress-test strategy challengers")
    }

    @ViewBuilder private var deepResearchControls: some View {
        if model.isRunningJob, let state = model.deepResearchControlState {
            HStack(spacing: 6) {
                if state == .paused {
                    Button("Resume", systemImage: "play.fill") { model.requestDeepResearchControl(.running) }
                } else if state == .running {
                    Button("Pause", systemImage: "pause.fill") { model.requestDeepResearchControl(.paused) }
                }
                Button("Stop", systemImage: "stop.fill", role: .destructive) {
                    model.requestDeepResearchControl(.stopped)
                }
                .disabled(state == .stopped)
                Text("Thermals: \(model.deepResearchThermalState)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .controlSize(.small)
        }
    }

    private func evaluateButton(compact: Bool) -> some View {
        Button {
            guard let context = selectedResearchContext else { return }
            let job = EngineJob.evaluateStrategies(
                strategyIDs: context.strategyIDs,
                mode: context.mode,
                asset: context.asset
            )
            Task { await model.run(job, configuration: settings.configuration) }
        } label: {
            actionLabel("Evaluate Selected", systemImage: "play.fill", compact: compact)
        }
        .buttonStyle(.borderedProminent)
        .disabled(model.isRunningJob || selectedResearchContext == nil)
        .accessibilityLabel("Evaluate selected strategies")
        .accessibilityIdentifier(StrategyLabAccessibility.evaluateButton)
        .help(model.strategySelectionIssue ?? "Evaluate selected strategies")
    }

    private func learnButton(compact: Bool) -> some View {
        Button {
            guard let strategy = model.selectedStrategy, let context = selectedResearchContext else { return }
            let configuration = settings.configuration.scoped(
                strategyIDs: context.strategyIDs,
                asset: context.asset
            )
            Task {
                await model.run(
                    .learn(assetID: strategy.symbol, interval: strategy.interval, budget: learningBudget),
                    configuration: configuration
                )
            }
        } label: {
            actionLabel("Learn", systemImage: "wand.and.stars", compact: compact)
        }
        .disabled(model.isRunningJob || model.learningSelectionIssue != nil)
        .accessibilityLabel("Run bounded learning")
        .accessibilityIdentifier(StrategyLabAccessibility.learnButton)
        .help(model.learningSelectionIssue ?? "Run bounded learning")
    }

    private func exportButton(compact: Bool) -> some View {
        Button {
            Task { await model.run(.exportSnapshot(databaseURL: nil), configuration: settings.configuration) }
        } label: {
            actionLabel("Export", systemImage: "square.and.arrow.down", compact: compact)
        }
        .disabled(model.isRunningJob)
        .accessibilityLabel("Export strategy snapshot")
        .accessibilityIdentifier(StrategyLabAccessibility.exportButton)
        .help("Export strategy snapshot")
    }

    private var budgetPicker: some View {
        Picker("Budget", selection: $learningBudget) {
            Text(StrategyLabLayout.budgetOptionTitle(20)).tag(20)
            Text(StrategyLabLayout.budgetOptionTitle(50)).tag(50)
            Text(StrategyLabLayout.budgetOptionTitle(100)).tag(100)
        }
        .frame(width: StrategyLabLayout.budgetPickerWidth)
        .help("Maximum candidates to evaluate")
    }

    @ViewBuilder private func actionLabel(_ title: String, systemImage: String, compact: Bool) -> some View {
        if compact {
            Image(systemName: systemImage)
        } else {
            Label(title, systemImage: systemImage)
        }
    }

    private func jobStatus(compact: Bool) -> some View {
        let status = StrategyJobStatusPresentation(
            isRunning: model.isRunningJob,
            progress: model.activeJobProgress,
            outcome: model.lastJobOutcome,
            selectionIssue: model.strategyActionStatusIssue
        )
        return HStack(spacing: 6) {
            if status.isRunning {
                if let value = status.progressValue {
                    ProgressView(value: value).frame(width: compact ? 48 : 72)
                } else {
                    ProgressView().controlSize(.small)
                }
            } else {
                Image(systemName: status.symbolName)
            }
            Text(status.message)
                .lineLimit(compact ? 2 : 1)
                .foregroundStyle(status.isFailure ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
                .font(compact ? .caption : .body)
                .fixedSize(horizontal: false, vertical: compact)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityLabel)
        .accessibilityIdentifier("strategyLab.jobStatus")
        .help(status.message)
    }

    private var strategyTable: some View {
        Table(presentation.strategies, selection: $model.selectedStrategyIDs) {
            TableColumn("Strategy") { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.strategy.strategyId.researchTitle).fontWeight(.medium)
                    Text("\(row.familyTitle) · \(row.strategy.symbol) · \(row.strategy.interval)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .width(min: 150, ideal: 170, max: 180)
            TableColumn("Research posture") { row in
                Label(row.postureTitle, systemImage: row.posture.symbolName)
                    .foregroundStyle(row.posture.color)
                    .accessibilityLabel(row.directionAccessibilityLabel)
            }
            .width(min: 125, ideal: 145)
            TableColumn("Weight") { row in Text(row.weightTitle).monospacedDigit() }
                .width(70)
            TableColumn("Development") { row in Text(row.developmentSharpe).monospacedDigit() }
                .width(90)
            TableColumn("Sealed final") { row in Text(row.finalSharpe).monospacedDigit() }
                .width(85)
            TableColumn("Evidence gate") { row in
                Label(row.promotionTitle, systemImage: row.strategy.causalAuditPassed == true ? "checkmark.shield" : "exclamationmark.triangle")
                    .accessibilityLabel(row.statusAccessibilityLabel)
            }
            .width(min: 120, ideal: 145)
        }
        .accessibilityIdentifier(StrategyLabAccessibility.table)
    }
}
