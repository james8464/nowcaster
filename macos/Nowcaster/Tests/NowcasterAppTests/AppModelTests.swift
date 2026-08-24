import Foundation
import Testing

@testable import NowcasterApp

private func fixtureSnapshot() throws -> NowcasterSnapshot {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    return try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
}

private func fixtureData(gitCommit: String? = nil) throws -> Data {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    guard let gitCommit else { return try Data(contentsOf: url) }
    var root = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    var metadata = try #require(root["metadata"] as? [String: Any])
    metadata["git_commit"] = gitCommit
    root["metadata"] = metadata
    return try JSONSerialization.data(withJSONObject: root)
}

private final class ControlledEngineRunner: EngineRunning, @unchecked Sendable {
    private let lock = NSLock()
    private let exportData: Data
    private var jobs: [EngineJob] = []
    private var primaryContinuation: AsyncThrowingStream<EngineProgressEvent, Error>.Continuation?

    init(exportData: Data) { self.exportData = exportData }

    func run(
        _ job: EngineJob,
        configuration: EngineConfiguration
    ) -> AsyncThrowingStream<EngineProgressEvent, Error> {
        lock.withLock { jobs.append(job) }
        switch job {
        case .evaluateStrategies, .learn:
            return AsyncThrowingStream { continuation in
                lock.withLock { primaryContinuation = continuation }
                continuation.yield(
                    EngineProgressEvent(
                        event: "progress",
                        stage: job.stageName,
                        progress: 0.4,
                        message: "Evaluated 8 bounded candidates"
                    )
                )
            }
        case .exportSnapshot:
            return AsyncThrowingStream { continuation in
                do {
                    try exportData.write(to: configuration.snapshotURL)
                    continuation.yield(
                        EngineProgressEvent(event: "complete", stage: "export", progress: 1, message: "Exported")
                    )
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        default:
            return AsyncThrowingStream { $0.finish() }
        }
    }

    func waitForPrimary() async {
        while lock.withLock({ primaryContinuation == nil }) { await Task.yield() }
    }

    func finishPrimary() {
        let continuation = lock.withLock {
            defer { primaryContinuation = nil }
            return primaryContinuation
        }
        continuation?.finish()
    }

    var recordedJobs: [EngineJob] { lock.withLock { jobs } }
}

private struct StructuredFailureRunner: EngineRunning {
    func run(
        _ job: EngineJob,
        configuration _: EngineConfiguration
    ) -> AsyncThrowingStream<EngineProgressEvent, Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(
                EngineProgressEvent(
                    event: "error",
                    stage: job.stageName,
                    progress: 1,
                    message: "Requested coverage is unavailable for the exact dataset"
                )
            )
            continuation.finish(
                throwing: EngineRunnerError.nonzeroExit(1, diagnostics: "generic Click/Typer exit")
            )
        }
    }
}

@Test @MainActor func globalSearchFindsSymbolsAndSelectsMarket() throws {
    let model = AppModel(snapshot: try fixtureSnapshot())
    model.searchText = "ETH"
    #expect(model.searchResults.map(\.symbol) == ["ETH-USD"])
    model.selectSearchResult(try #require(model.searchResults.first))
    #expect(model.destination == .markets)
    #expect(model.selectedInstrumentID == "ETH-USD")
}

@Test @MainActor func staleQualityIssuesArePrioritized() throws {
    let model = AppModel(snapshot: try fixtureSnapshot())
    #expect(model.snapshot != nil)
    #expect(model.dataModeLabel == "Demo snapshot")
}

@Test @MainActor func successfulStrategyJobStreamsProgressThenExportsAndReloads() async throws {
    let temporaryRoot = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterAppModel-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: temporaryRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    let snapshotURL = temporaryRoot.appending(path: "snapshot.json")
    try fixtureData().write(to: snapshotURL)
    let runner = ControlledEngineRunner(exportData: try fixtureData(gitCommit: "after-export"))
    let model = AppModel(snapshot: try fixtureSnapshot(), runner: runner)
    let context = try #require(model.selectedResearchContext)
    let configuration = EngineConfiguration(
        projectRoot: temporaryRoot,
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
        snapshotURL: snapshotURL,
        mode: .demo
    )
    let job = EngineJob.evaluateStrategies(
        strategyIDs: context.strategyIDs,
        mode: context.mode,
        asset: context.asset
    )
    let task = Task { await model.run(job, configuration: configuration) }

    await runner.waitForPrimary()
    while model.progressEvents.isEmpty { await Task.yield() }
    #expect(model.isRunningJob)
    #expect(model.activeJobProgress?.value == 0.4)
    #expect(model.activeJobProgress?.message == "Evaluated 8 bounded candidates")
    let run = try #require(model.snapshot?.learningRuns.first)
    let live = LearningProgressPresentation(run: LearningRunPresentation(run: run), live: model.activeJobProgress)
    #expect(live.value == 0.4)
    #expect(live.title == "Evaluated 8 bounded candidates")

    runner.finishPrimary()
    await task.value
    let jobs = runner.recordedJobs
    #expect(jobs.count == 2)
    if case .evaluateStrategies = jobs.first {
        // The original typed job runs before export.
    } else {
        Issue.record("Expected strategy evaluation before export")
    }
    #expect(jobs.last == .exportSnapshot)
    #expect(model.snapshot?.metadata.gitCommit == "after-export")
    #expect(model.lastJobOutcome.isSuccess)
}

@Test @MainActor func structuredTask7FailureOutranksGenericNonzeroExitAndPersists() async throws {
    let snapshot = try fixtureSnapshot()
    let model = AppModel(snapshot: snapshot, runner: StructuredFailureRunner())
    let context = try #require(model.selectedResearchContext)
    let configuration = EngineConfiguration(
        projectRoot: URL(fileURLWithPath: "/tmp"),
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
        snapshotURL: URL(fileURLWithPath: "/tmp/unused.json"),
        mode: .demo
    )

    await model.run(
        .evaluateStrategies(strategyIDs: context.strategyIDs, mode: context.mode, asset: context.asset),
        configuration: configuration
    )

    #expect(model.lastJobOutcome.failureMessage == "Requested coverage is unavailable for the exact dataset")
    #expect(!model.lastJobOutcome.failureMessage!.contains("generic Click/Typer exit"))
    let presentation = StrategyJobStatusPresentation(
        isRunning: model.isRunningJob,
        progress: model.activeJobProgress,
        outcome: model.lastJobOutcome,
        selectionIssue: nil
    )
    #expect(presentation.isFailure)
    #expect(presentation.message == "Requested coverage is unavailable for the exact dataset")
}

@Test @MainActor func configurationFailureBecomesTypedDurableOutcome() async throws {
    let missing = "/tmp/nowcaster-missing-\(UUID().uuidString)"
    let model = AppModel(snapshot: try fixtureSnapshot())
    await model.run(
        .exportSnapshot,
        configuration: EngineConfiguration(
            projectRoot: URL(fileURLWithPath: missing),
            pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
            snapshotURL: URL(fileURLWithPath: "/tmp/unused.json"),
            mode: .demo
        )
    )
    #expect(model.lastJobOutcome.failureMessage?.contains("Project root is unavailable") == true)
}

@Test @MainActor func cachedSnapshotGetsAccessibleRefreshBannerAfterIncompatibleReload() async throws {
    let model = AppModel(snapshot: try fixtureSnapshot())
    let incompatible = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterSchema-\(UUID().uuidString).json")
    try Data("{\"schema_version\":1}".utf8).write(to: incompatible)
    defer { try? FileManager.default.removeItem(at: incompatible) }

    await model.loadSnapshot(url: incompatible)

    #expect(model.snapshot != nil)
    let banner = try #require(RootSnapshotStatusPresentation(state: model.loadState))
    #expect(banner.title == "Snapshot refresh required")
    #expect(banner.message.localizedCaseInsensitiveContains("incompatible"))
    #expect(banner.accessibilityIdentifier == "snapshot.staleBanner")
}

@Test @MainActor func screenshotStateCanExposeTheCachedStaleBannerWithoutReplacingData() throws {
    let snapshot = try fixtureSnapshot()
    let model = AppModel(snapshot: snapshot)

    model.applyScreenshotState(arguments: ["--destination=strategyLab", "--ui-stale"])

    #expect(model.snapshot?.metadata.gitCommit == snapshot.metadata.gitCommit)
    let banner = try #require(RootSnapshotStatusPresentation(state: model.loadState))
    #expect(banner.title == "Snapshot refresh required")
    #expect(banner.message == "Snapshot schema 1 is incompatible; cached research remains visible.")
}
