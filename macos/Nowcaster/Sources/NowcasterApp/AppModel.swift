import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    var destination: AppDestination = .today
    var searchText = ""
    var selectedInstrumentID: String?
    var selectedEarningsID: String?
    var selectedSignalID: String?
    var selectedBacktestID: String?
    private(set) var snapshot: NowcasterSnapshot?
    private(set) var loadState: SnapshotLoadState = .idle
    private(set) var isRunningJob = false
    private(set) var progressEvents: [EngineProgressEvent] = []
    private let repository: SnapshotRepository
    private let runner: EngineRunner

    init(
        snapshot: NowcasterSnapshot? = nil,
        repository: SnapshotRepository = SnapshotRepository(),
        runner: EngineRunner = EngineRunner()
    ) {
        self.snapshot = snapshot
        self.repository = repository
        self.runner = runner
        loadState = snapshot == nil ? .idle : .loaded
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

    func selectSearchResult(_ instrument: InstrumentSnapshot) {
        selectedInstrumentID = instrument.id
        destination = .markets
        searchText = ""
    }

    func selectSignal(_ signal: ResearchSignalSnapshot) {
        selectedSignalID = signal.id
        destination = .signals
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

    func loadSnapshot(url: URL) async {
        loadState = .loading
        do {
            snapshot = try await repository.load(url: url)
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
        defer { isRunningJob = false }
        do {
            for try await event in runner.run(job, configuration: configuration) {
                progressEvents.append(event)
                if progressEvents.count > 200 {
                    progressEvents.removeFirst(progressEvents.count - 200)
                }
            }
            await loadSnapshot(url: configuration.snapshotURL)
        } catch {
            progressEvents.append(EngineProgressEvent(event: "job_failed", message: error.localizedDescription))
        }
    }
}
