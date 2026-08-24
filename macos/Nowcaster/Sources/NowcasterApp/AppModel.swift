import Foundation
import Observation

struct SelectedStrategyResearchContext: Equatable, Sendable {
    let datasetHash: String
    let cohortId: String
    let mode: StrategyRunMode
    let asset: StrategyAssetContext
    let strategyIDs: [String]
}

struct ActiveJobProgress: Equatable, Sendable {
    let stage: String?
    let value: Double?
    let message: String
}

enum EngineJobOutcome: Equatable, Sendable {
    case idle
    case running(EngineJob)
    case success(EngineJob, message: String)
    case failure(EngineJob, message: String)

    var failureMessage: String? {
        guard case let .failure(_, message) = self else { return nil }
        return message
    }

    var isSuccess: Bool {
        if case .success = self { return true }
        return false
    }
}

@MainActor
@Observable
final class AppModel {
    var destination: AppDestination = .today
    var searchText = ""
    var selectedInstrumentID: String?
    var selectedEarningsID: String?
    var selectedSignalID: String?
    var selectedBacktestID: String?
    var selectedStrategyIDs: Set<String> = []
    var selectedLearningRunID: String?
    private(set) var snapshot: NowcasterSnapshot?
    private(set) var loadState: SnapshotLoadState = .idle
    private(set) var isRunningJob = false
    private(set) var progressEvents: [EngineProgressEvent] = []
    private(set) var lastJobOutcome: EngineJobOutcome = .idle
    private let repository: SnapshotRepository
    private let runner: any EngineRunning

    init(
        snapshot: NowcasterSnapshot? = nil,
        repository: SnapshotRepository = SnapshotRepository(),
        runner: any EngineRunning = EngineRunner()
    ) {
        self.snapshot = snapshot
        self.repository = repository
        self.runner = runner
        loadState = snapshot == nil ? .idle : .loaded
        if let argument = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix("--destination=") }),
           let requested = AppDestination(rawValue: String(argument.dropFirst("--destination=".count))) {
            destination = requested
        }
        prepareDefaultSelections()
    }

    var searchResults: [InstrumentSnapshot] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return [] }
        return (snapshot?.instruments ?? [])
            .filter {
                $0.symbol.localizedCaseInsensitiveContains(query)
                    || $0.displayName.localizedCaseInsensitiveContains(query)
            }
            .sorted {
                let options: String.CompareOptions = [.caseInsensitive, .anchored]
                let firstStarts = $0.symbol.range(of: query, options: options) != nil
                let secondStarts = $1.symbol.range(of: query, options: options) != nil
                return firstStarts == secondStarts ? $0.symbol < $1.symbol : firstStarts
            }
            .prefix(10)
            .map { $0 }
    }

    var dataModeLabel: String {
        switch snapshot?.metadata.dataMode {
        case "demo_real_snapshot": "Demo snapshot"
        case "live_provider": "Live providers"
        case let value?: value.replacingOccurrences(of: "_", with: " ").capitalized
        case nil: "No data"
        }
    }

    var selectedInstrument: InstrumentSnapshot? {
        snapshot?.instruments.first { $0.id == selectedInstrumentID }
    }

    var selectedEarnings: EarningsSnapshot? {
        snapshot?.earnings.first { $0.id == selectedEarningsID }
    }

    var selectedSignal: ResearchSignalSnapshot? {
        snapshot?.signals.first { $0.id == selectedSignalID }
    }

    var selectedBacktest: BacktestSnapshot? {
        snapshot?.backtests.first { $0.id == selectedBacktestID }
    }

    var selectedStrategies: [StrategySnapshot] {
        snapshot?.strategies.filter { selectedStrategyIDs.contains($0.id) } ?? []
    }

    var selectedStrategy: StrategySnapshot? {
        selectedStrategies.first
    }

    var selectedResearchContext: SelectedStrategyResearchContext? {
        strategySelectionResolution.context
    }

    var strategySelectionIssue: String? {
        strategySelectionResolution.issue
    }

    var learningSelectionIssue: String? {
        guard selectedResearchContext != nil else { return strategySelectionIssue }
        return selectedStrategies.count == 1 ? nil : "Select exactly one strategy for bounded learning."
    }

    var strategyActionStatusIssue: String? {
        strategySelectionIssue ?? learningSelectionIssue
    }

    var selectedLearningRun: LearningRunSnapshot? {
        snapshot?.learningRuns.first { $0.id == selectedLearningRunID }
    }

    var activeJobProgress: ActiveJobProgress? {
        guard isRunningJob,
              let event = progressEvents.reversed().first(where: {
                  $0.progress != nil || $0.message?.isEmpty == false || $0.stage?.isEmpty == false
              })
        else { return nil }
        return ActiveJobProgress(
            stage: event.stage,
            value: event.progress.map { min(max($0, 0), 1) },
            message: event.message ?? event.stage ?? "Running research job"
        )
    }

    func selectSearchResult(_ instrument: InstrumentSnapshot) {
        selectedInstrumentID = instrument.id
        destination = .markets
        searchText = ""
    }

    func selectSignal(_ signal: ResearchSignalSnapshot) {
        selectedSignalID = signal.id
        destination = .signals
    }

    func selectStrategies(_ identifiers: Set<String>) {
        selectedStrategyIDs = identifiers
    }

    func loadBundledSnapshot() async {
        guard snapshot == nil else { return }
        guard let url = Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        ) else {
            loadState = .failure("The bundled research snapshot is missing.")
            return
        }
        await loadSnapshot(url: url)
    }

    func applyScreenshotState(arguments: [String]) {
        guard snapshot != nil,
              arguments.contains(where: { $0.hasPrefix("--destination=") }),
              arguments.contains("--ui-stale")
        else { return }
        loadState = .stale("Snapshot schema 1 is incompatible; cached research remains visible.")
    }

    func loadSnapshot(url: URL) async {
        loadState = .loading
        do {
            snapshot = try await repository.load(url: url)
            prepareDefaultSelections()
            loadState = .loaded
        } catch let SnapshotRepositoryError.incompatibleSchema(version) {
            loadState = snapshot == nil ? .incompatible(version) : .stale("Snapshot schema \(version) is incompatible")
        } catch {
            loadState = snapshot == nil ? .failure(error.localizedDescription) : .stale(error.localizedDescription)
        }
    }

    func run(_ job: EngineJob, configuration: EngineConfiguration) async {
        guard !isRunningJob else { return }
        isRunningJob = true
        progressEvents = []
        lastJobOutcome = .running(job)
        defer { isRunningJob = false }
        do {
            try await consume(job, configuration: configuration)
            if let exportJob = job.followUpExport(configuration: configuration) {
                try await consume(exportJob, configuration: configuration)
            }
            await loadSnapshot(url: configuration.snapshotURL)
            switch loadState {
            case .loaded:
                lastJobOutcome = .success(job, message: "Snapshot exported and reloaded.")
            case let .stale(message), let .failure(message):
                lastJobOutcome = .failure(job, message: "Snapshot refresh failed: \(message)")
            case let .incompatible(version):
                lastJobOutcome = .failure(job, message: "Snapshot schema \(version) is incompatible after refresh.")
            case .idle, .loading:
                lastJobOutcome = .failure(job, message: "Snapshot refresh did not complete.")
            }
        } catch {
            let preferred = progressEvents.reversed().first {
                $0.event == "error" && $0.message?.isEmpty == false
            }?.message
            let message = preferred ?? error.localizedDescription
            lastJobOutcome = .failure(job, message: message)
            appendProgress(EngineProgressEvent(event: "job_failed", stage: job.stageName, message: message))
        }
    }

    private func consume(_ job: EngineJob, configuration: EngineConfiguration) async throws {
        for try await event in runner.run(job, configuration: configuration) {
            appendProgress(event)
        }
    }

    private func appendProgress(_ event: EngineProgressEvent) {
        progressEvents.append(event)
        if progressEvents.count > 200 {
            progressEvents.removeFirst(progressEvents.count - 200)
        }
    }

    private func prepareDefaultSelections() {
        guard let snapshot else { return }
        selectedInstrumentID = selectedInstrumentID ?? snapshot.instruments.first?.id
        selectedEarningsID = selectedEarningsID ?? snapshot.earnings.first?.id
        selectedSignalID = selectedSignalID ?? SignalListModel(signals: snapshot.signals).visibleSignals.first?.id
        selectedBacktestID = selectedBacktestID ?? snapshot.backtests.last?.id
        let availableStrategyIDs = Set(snapshot.strategies.map(\.id))
        selectedStrategyIDs.formIntersection(availableStrategyIDs)
        if selectedStrategyIDs.isEmpty, let first = snapshot.strategies.first {
            selectedStrategyIDs = [first.id]
        }
        if !snapshot.learningRuns.contains(where: { $0.id == selectedLearningRunID }) {
            selectedLearningRunID = snapshot.learningRuns.first?.id
        }
    }

    private var strategySelectionResolution: (context: SelectedStrategyResearchContext?, issue: String?) {
        guard let snapshot, !selectedStrategies.isEmpty else {
            return (nil, "Select at least one strategy.")
        }
        guard selectedStrategies.allSatisfy({ $0.cohortId != nil }) else {
            return (nil, "Legacy strategy context is incomplete. Export a current schema v2 snapshot.")
        }
        let signatures = Set(selectedStrategies.map {
            [$0.datasetHash, $0.symbol, $0.interval, $0.mode, $0.cohortId ?? ""]
                .joined(separator: "\u{1F}")
        })
        guard signatures.count == 1, let first = selectedStrategies.first else {
            return (
                nil,
                "Selected strategies must share the same dataset, source, asset, interval, mode, and cohort."
            )
        }
        guard let interval = StrategyInterval(rawValue: first.interval),
              let mode = StrategyRunMode(rawValue: first.mode)
        else { return (nil, "The selected strategy interval or mode is unsupported.") }
        let matchingCoverage = snapshot.datasetCoverage.filter {
            $0.datasetHash == first.datasetHash && $0.symbol == first.symbol && $0.interval == first.interval
                && $0.complete
        }
        let sources = Set(matchingCoverage.map { "\($0.provider)\u{1F}\($0.feed)" })
        guard sources.count == 1,
              let coverage = matchingCoverage.first,
              let provider = StrategyProvider(rawValue: coverage.provider)
        else {
            return (nil, "Complete source coverage for the exact selected dataset is unavailable or ambiguous.")
        }
        let ids = Array(Set(selectedStrategies.map(\.strategyId))).sorted()
        return (
            SelectedStrategyResearchContext(
                datasetHash: first.datasetHash,
                cohortId: first.cohortId ?? "",
                mode: mode,
                asset: StrategyAssetContext(
                    provider: provider,
                    feed: coverage.feed,
                    symbol: first.symbol,
                    interval: interval
                ),
                strategyIDs: ids
            ),
            nil
        )
    }
}
