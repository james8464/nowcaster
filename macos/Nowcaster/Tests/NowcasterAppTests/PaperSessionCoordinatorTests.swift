import Foundation
import Testing
@testable import NowcasterApp

@MainActor private final class Collector: PaperSessionCollecting {
    var isRunning = false
    var starts = 0
    var suspended: CheckedContinuation<Void, Never>?
    var suspendStart = false
    var shutdownBarrier: DrainBarrier?
    var selectedSource: PaperSessionSource? = .init(directory: URL(fileURLWithPath: "/tmp/synthetic"), protocolHash: String(repeating: "a", count: 64))
    func startCollection() async throws {
        starts += 1
        if suspendStart { await withCheckedContinuation { suspended = $0 } }
        isRunning = true
    }
    func shutdown(timeout: Duration) async -> Bool { await shutdownBarrier?.arrive("collector", timeout: timeout); isRunning = false; return true }
}

@MainActor private final class Research: PaperSessionResearching {
    var status: LearningStatus?
    var isRunning = false
    var starts = 0
    var pauses: [String] = []
    var resumes = 0
    var shutdownBarrier: DrainBarrier?
    var onStatus: (@MainActor (LearningStatus) -> Void)?
    var preparationBarrier: DrainBarrier?
    func prepare(preferences: PaperSessionPreferences, configuration: EngineConfiguration) async throws -> PaperSessionPreferences {
        await preparationBarrier?.arrive("prepare", timeout: .zero)
        return preferences
    }
    func start(campaignHash: String, registryURL: URL, configuration: EngineConfiguration) async throws { starts += 1; isRunning = true }
    func pause(reason: String) async { pauses.append(reason) }
    func resume() async throws { resumes += 1 }
    func shutdown(timeout: Duration) async -> Bool { await shutdownBarrier?.arrive("research", timeout: timeout); isRunning = false; return true }
}

@MainActor private final class DrainBarrier {
    var timeouts: [String: Duration] = [:]
    var continuations: [CheckedContinuation<Void, Never>] = []
    var released = false
    func arrive(_ name: String, timeout: Duration) async {
        timeouts[name] = timeout
        if !released { await withCheckedContinuation { continuations.append($0) } }
    }
    func release() { released = true; for continuation in continuations { continuation.resume() }; continuations = [] }
}

@MainActor private func session(_ collector: Collector, _ research: Research, learning: Bool = true,
                               monitor: BackgroundResourceMonitor = BackgroundResourceMonitor(sample: { .healthy })) -> PaperSessionCoordinator {
    var preferences = PaperSessionPreferences()
    preferences.learningEnabled = learning
    preferences.source = collector.selectedSource
    preferences.campaignHash = String(repeating: "b", count: 64)
    preferences.registryURL = URL(fileURLWithPath: "/tmp/synthetic-registry")
    return PaperSessionCoordinator(collector: collector, research: research, preferences: preferences,
        configuration: EngineConfiguration(projectRoot: URL(fileURLWithPath: "/tmp"), pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"), snapshotURL: URL(fileURLWithPath: "/tmp/snapshot"), mode: .demo),
        monitor: monitor)
}

@Test @MainActor func menuBarVisibilityIgnoresRepeatedFrameworkValues() throws {
    let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = PaperSessionPreferenceStore(url: directory.appending(path: "preferences.json"))
    let owner = PaperSessionCoordinator(collector: Collector(), research: Research(),
        configuration: EngineConfiguration(projectRoot: directory, pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"),
            snapshotURL: directory.appending(path: "snapshot"), mode: .demo), store: store)
    owner.setShowMenuBarExtra(false)
    #expect(!FileManager.default.fileExists(atPath: store.url.path))
    owner.setShowMenuBarExtra(true)
    #expect(store.load().preferences.showMenuBarExtra)
    owner.setShowMenuBarExtra(false)
    #expect(!store.load().preferences.showMenuBarExtra)
}

@Test @MainActor func paperSessionMonitorSurvivesDisableDuringPreparation() async {
    let collector = Collector(), research = Research(), barrier = DrainBarrier()
    research.preparationBarrier = barrier
    var samples = 0
    let monitor = BackgroundResourceMonitor(sample: {
        samples += 1
        return samples == 1 ? .init(thermal: .unknown) : .healthy
    })
    let owner = session(collector, research, monitor: monitor)
    let start = Task { await owner.start() }
    while barrier.timeouts["prepare"] == nil { await Task.yield() }
    for _ in 0..<100 where samples < 2 { await Task.yield() }
    await owner.setLearningEnabled(false)
    barrier.release(); await start.value
    await owner.setLearningEnabled(true)
    #expect(collector.isRunning)
    #expect(research.isRunning)
    #expect(owner.state == .waiting)
    _ = await owner.shutdown()
}

@Test @MainActor func paperSessionDisableCompletionCannotOverwriteNewerPause() async {
    let collector = Collector(), research = Research(), barrier = DrainBarrier()
    let owner = session(collector, research)
    await owner.start(); research.shutdownBarrier = barrier
    let disable = Task { await owner.setLearningEnabled(false) }
    while barrier.continuations.count < 1 { await Task.yield() }
    let pause = Task { await owner.pause() }
    while barrier.continuations.count < 2 { await Task.yield() }
    // Release newer Pause before the earlier disable operation.
    barrier.continuations.removeLast().resume()
    await pause.value
    #expect(owner.state == .paused)
    barrier.release(); await disable.value
    #expect(owner.state == .paused)
    #expect(!collector.isRunning)
}

@Test @MainActor func paperSessionRapidLearningToggleWaitsForPreviousDrain() async {
    let collector = Collector(), research = Research(), barrier = DrainBarrier()
    let owner = session(collector, research)
    await owner.start(); research.shutdownBarrier = barrier
    let disable = Task { await owner.setLearningEnabled(false) }
    while barrier.continuations.isEmpty { await Task.yield() }
    let enable = Task { await owner.setLearningEnabled(true) }
    for _ in 0..<100 { await Task.yield() }
    #expect(research.starts == 1)
    barrier.release(); await disable.value; await enable.value
    #expect(owner.preferences.learningEnabled)
    #expect(research.isRunning)
    #expect(owner.state == .waiting)
    _ = await owner.shutdown()
}

@Test @MainActor func paperSessionTwoConsumersStartOnlyOneChildAndCanReopen() async {
    let collector = Collector(), research = Research()
    let shared = session(collector, research)
    await shared.start(); await shared.start()
    #expect(collector.starts == 1); #expect(research.starts == 1)
    let reopened = shared
    await reopened.start()
    #expect(research.starts == 1)
    _ = await shared.shutdown()
}

@Test @MainActor func paperSessionQuitDrainsCollectionAndResearchConcurrentlyWithinOneBudget() async {
    let collector = Collector(), research = Research(), barrier = DrainBarrier()
    let owner = session(collector, research)
    await owner.start()
    collector.shutdownBarrier = barrier; research.shutdownBarrier = barrier
    let shutdown = Task { await owner.shutdown() }
    for _ in 0..<100 where barrier.timeouts.count < 2 { await Task.yield() }
    #expect(barrier.timeouts.count == 2)
    #expect(barrier.timeouts.values.allSatisfy { $0 <= .seconds(30) })
    barrier.release()
    #expect(await shutdown.value)
    #expect(await owner.shutdown())
}

@Test @MainActor func paperSessionRapidPauseRejectsLateStartPublication() async {
    let collector = Collector(), research = Research()
    collector.suspendStart = true
    let owner = session(collector, research)
    let start = Task { await owner.start() }
    while collector.suspended == nil { await Task.yield() }
    await owner.pause()
    collector.suspended?.resume(); await start.value
    #expect(owner.state == .paused); #expect(!collector.isRunning); #expect(research.starts == 0)
}

@Test @MainActor func paperSessionUserPauseWinsOverResourceRecoveryAndLearningOffKeepsCollection() async {
    let collector = Collector(), research = Research(), owner: PaperSessionCoordinator
    owner = session(collector, research)
    await owner.start()
    await owner.resourcesChanged(.init(thermal: .serious))
    await owner.resourcesChanged(.healthy)
    #expect(research.resumes == 1)
    await owner.pause()
    await owner.resourcesChanged(.healthy)
    #expect(research.resumes == 1); #expect(owner.state == .paused)
    await owner.start()
    await owner.setLearningEnabled(false)
    #expect(collector.isRunning); #expect(!research.isRunning); #expect(owner.state == .collecting)
    _ = await owner.shutdown()
}

@Test @MainActor func paperSessionRuntimeResourcePauseRequiresRecoveryAndUserPauseWins() async throws {
    let collector = Collector(), research = Research(), owner: PaperSessionCoordinator
    owner = session(collector, research)
    await owner.start()
    let status = try LearningStatus.decode(Data(backgroundStatusFixture(state: "paused", reason: "resource_preempted").utf8))
    research.onStatus?(status)
    await owner.resourcesChanged(.healthy)
    #expect(research.resumes == 0)
    await owner.resourcesChanged(.init(thermal: .critical))
    await owner.resourcesChanged(.healthy)
    #expect(research.resumes == 1)
    research.onStatus?(status)
    await owner.pause(); await owner.resourcesChanged(.healthy)
    #expect(research.resumes == 1)
    _ = await owner.shutdown()
}

@Test @MainActor func paperSessionChangedSourceBlocksOptedInRestore() async {
    let collector = Collector(), research = Research(), owner: PaperSessionCoordinator
    owner = session(collector, research)
    await owner.setResumeOnLaunch(true)
    collector.selectedSource = .init(directory: URL(fileURLWithPath: "/tmp/other"), protocolHash: String(repeating: "c", count: 64))
    await owner.restoreIfOptedIn(); await owner.restoreIfOptedIn()
    #expect(collector.starts == 0); #expect(research.starts == 0)
    guard case .blocked = owner.state else { Issue.record("Changed source must block restore"); return }
}

@Test func backgroundResearchAllResourceReasonsAndWorkerBounds() {
    for snapshot in [BackgroundResourceSnapshot(thermal: .serious), .init(thermal: .critical), .init(thermal: .unknown), .init(lowPower: true), .init(memoryAdmissible: false), .init(diskAdmissible: false), .init(collectorHealthy: false)] {
        #expect(BackgroundResourcePolicy.shouldPause(snapshot))
    }
    #expect(!BackgroundResourcePolicy.shouldPause(.healthy))
    #expect([1, 2, 4, 8].map(BackgroundResourcePolicy.efficientWorkers) == [1, 1, 2, 4])
}

@Test @MainActor func backgroundResearchResourceMonitorStartsConservativelyBeforeAsyncProbe() {
    let monitor = BackgroundResourceMonitor()
    #expect(BackgroundResourcePolicy.shouldPause(monitor.current()))
}
