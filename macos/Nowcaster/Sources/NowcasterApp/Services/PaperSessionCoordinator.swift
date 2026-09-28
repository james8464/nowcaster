import Foundation
import Observation

@MainActor protocol PaperSessionCollecting: AnyObject, Sendable {
    var isRunning: Bool { get }
    var selectedSource: PaperSessionSource? { get }
    var collectionHealthy: Bool { get }
    func selectSource(_ source: PaperSessionSource, configuration: EngineConfiguration) async throws
    func startCollection() async throws
    func shutdown(timeout: Duration) async -> Bool
}
extension PaperSessionCollecting {
    var collectionHealthy: Bool { isRunning }
    func selectSource(_ source: PaperSessionSource, configuration: EngineConfiguration) async throws {
        guard selectedSource == source else { throw BackgroundResearchError.identityMismatch }
    }
}

@MainActor protocol PaperSessionResearching: AnyObject, Sendable {
    var status: LearningStatus? { get }
    var isRunning: Bool { get }
    var onStatus: (@MainActor (LearningStatus) -> Void)? { get set }
    var isStarting: Bool { get }
    var onFailure: (@MainActor (String) -> Void)? { get set }
    var resourcePauseReason: String? { get }
    func prepare(preferences: PaperSessionPreferences, configuration: EngineConfiguration) async throws -> PaperSessionPreferences
    func start(campaignHash: String, registryURL: URL, configuration: EngineConfiguration) async throws
    func pause(reason: String) async
    func resume() async throws
    func shutdown(timeout: Duration) async -> Bool
}
extension PaperSessionResearching {
    var isStarting: Bool { false }
    var resourcePauseReason: String? { nil }
    var onFailure: (@MainActor (String) -> Void)? { get { nil } set {} }
    func prepare(preferences: PaperSessionPreferences, configuration: EngineConfiguration) async throws -> PaperSessionPreferences { preferences }
}

@MainActor @Observable final class PaperSessionCoordinator {
    private(set) var state: PaperSessionState = .idle
    private(set) var preferences: PaperSessionPreferences
    private(set) var explanation: String?
    @ObservationIgnored private let collector: any PaperSessionCollecting
    @ObservationIgnored private let research: any PaperSessionResearching
    @ObservationIgnored private let monitor: BackgroundResourceMonitor
    @ObservationIgnored private let store: PaperSessionPreferenceStore?
    @ObservationIgnored private var configuration: EngineConfiguration
    @ObservationIgnored private var epoch = UUID()
    @ObservationIgnored private var restored = false
    @ObservationIgnored private var active = false
    @ObservationIgnored private var userPaused = false
    @ObservationIgnored private var automaticPause = false
    @ObservationIgnored private var observedResourcePressure = false
    @ObservationIgnored private var resources = BackgroundResourceSnapshot.healthy
    @ObservationIgnored private var shutdownTask: Task<Bool, Never>?

    init(collector: any PaperSessionCollecting, research: any PaperSessionResearching,
         preferences: PaperSessionPreferences = .init(), configuration: EngineConfiguration,
         monitor: BackgroundResourceMonitor = BackgroundResourceMonitor(), store: PaperSessionPreferenceStore? = nil,
         explanation: String? = nil) {
        self.collector = collector; self.research = research; self.preferences = preferences
        self.configuration = configuration; self.monitor = monitor; self.store = store; self.explanation = explanation
        self.resources = monitor.current()
        research.onStatus = { [weak self] status in self?.received(status) }
        research.onFailure = { [weak self] message in
            guard let self, self.active, !self.userPaused else { return }
            self.state = .blocked(message); self.explanation = message
        }
    }

    func configure(_ configuration: EngineConfiguration) { self.configuration = configuration }

    func start() async {
        guard !active, state != .starting, state != .pausing, shutdownTask == nil else { return }
        let token = UUID(); epoch = token; userPaused = false; state = .starting; explanation = nil
        do {
            if let source = preferences.source {
                try await collector.selectSource(source, configuration: configuration)
            }
            guard epoch == token else { return }
            guard let source = collector.selectedSource else { throw BackgroundResearchError.missingRegistration }
            if let retained = preferences.source, retained != source { throw BackgroundResearchError.identityMismatch }
            preferences.source = source
            try persist()
            try await collector.startCollection()
            guard epoch == token else { _ = await collector.shutdown(timeout: .seconds(30)); return }
            guard collector.isRunning else { throw BackgroundResearchError.missingRegistration }
            resources.collectorHealthy = collector.collectionHealthy
            active = true
            if preferences.learningEnabled { try await startResearch(token: token) }
            guard epoch == token else { return }
            state = preferences.learningEnabled ? (automaticPause ? .paused : research.isStarting ? .starting : .waiting) : .collecting
            monitor.start { [weak self] snapshot in
                guard let self else { return }
                var snapshot = snapshot; snapshot.collectorHealthy = self.collector.collectionHealthy
                await self.resourcesChanged(snapshot)
            }
        } catch {
            guard epoch == token else { return }
            active = false; state = .blocked(error.localizedDescription); explanation = error.localizedDescription
        }
    }

    private func startResearch(token: UUID) async throws {
        if preferences.campaignID == nil {
            preferences.campaignID = UUID().uuidString
            preferences.createdAt = ISO8601DateFormatter().string(from: Date())
            let root = PaperSessionPreferenceStore.application.url.deletingLastPathComponent().appending(path: "BackgroundResearch")
            preferences.registryURL = root.appending(path: "registry")
            preferences.manifestURL = root.appending(path: "manifests/\(preferences.campaignID!).json")
            // Persist preparation identity before starting the cancellable helper.
            try persist()
        }
        let prepared = try await research.prepare(preferences: preferences, configuration: configuration)
        guard epoch == token, preferences.learningEnabled else { return }
        preferences = prepared; try persist()
        guard let hash = preferences.campaignHash, let registry = preferences.registryURL else { throw BackgroundResearchError.missingRegistration }
        if BackgroundResourcePolicy.shouldPause(resources) { automaticPause = true; observedResourcePressure = true; return }
        try await research.start(campaignHash: hash, registryURL: registry, configuration: configuration)
    }

    func pause() async {
        guard shutdownTask == nil else { return }
        epoch = UUID(); active = false; userPaused = true; automaticPause = false; state = .pausing; monitor.stop()
        async let researchStopped = research.shutdown(timeout: .seconds(30))
        async let collectorStopped = collector.shutdown(timeout: .seconds(30))
        let stopped = await (researchStopped, collectorStopped)
        state = stopped.0 && stopped.1 ? .paused : .blocked(BackgroundResearchError.interrupted.localizedDescription)
    }

    func shutdown() async -> Bool {
        if let shutdownTask { return await shutdownTask.value }
        epoch = UUID(); active = false; userPaused = true; automaticPause = false; state = .pausing; monitor.stop()
        let task = Task { @MainActor in
            async let researchStopped = research.shutdown(timeout: .seconds(30))
            async let collectorStopped = collector.shutdown(timeout: .seconds(30))
            let result = await (researchStopped, collectorStopped)
            state = result.0 && result.1 ? .paused : .blocked(BackgroundResearchError.interrupted.localizedDescription)
            return result.0 && result.1
        }
        shutdownTask = task
        return await task.value
    }

    func restoreIfOptedIn() async {
        guard !restored else { return }; restored = true
        guard preferences.resumeOnLaunch else { return }
        guard preferences.source != nil else { state = .blocked("Saved paper desk is unavailable."); return }
        await start()
    }

    func setLearningEnabled(_ enabled: Bool) async {
        preferences.learningEnabled = enabled
        do { try persist() } catch { state = .blocked(error.localizedDescription); return }
        guard active else { return }
        if !enabled {
            epoch = UUID(); automaticPause = false
            _ = await research.shutdown(timeout: .seconds(30))
            state = .collecting
        } else {
            let token = UUID(); epoch = token; state = .starting
            do {
                try await startResearch(token: token)
                if epoch == token { state = automaticPause ? .paused : research.isStarting ? .starting : .waiting }
            } catch { if epoch == token { state = .blocked(error.localizedDescription) } }
        }
    }

    func setResumeOnLaunch(_ enabled: Bool) async {
        preferences.resumeOnLaunch = enabled
        do { try persist() } catch { state = .blocked(error.localizedDescription) }
    }
    func setShowMenuBarExtra(_ enabled: Bool) {
        preferences.showMenuBarExtra = enabled
        do { try persist() } catch { state = .blocked(error.localizedDescription) }
    }
    func setResourceProfile(_ profile: PaperSessionPreferences.ResourceProfile) async {
        guard preferences.resourceProfile != profile else { return }
        preferences.resourceProfile = profile
        do { try persist() } catch { state = .blocked(error.localizedDescription); return }
        await retryResearch()
    }

    func retryResearch() async {
        guard active, preferences.learningEnabled, !userPaused, state != .starting else { return }
        let token = UUID(); epoch = token; automaticPause = false; observedResourcePressure = false; state = .starting
        guard await research.shutdown(timeout: .seconds(30)) else {
            if epoch == token { state = .blocked(BackgroundResearchError.interrupted.localizedDescription) }
            return
        }
        guard epoch == token, active, !userPaused else { return }
        do {
            try await startResearch(token: token)
            if epoch == token { state = automaticPause ? .paused : research.isStarting ? .starting : .waiting; explanation = nil }
        } catch { if epoch == token { state = .blocked(error.localizedDescription) } }
    }

    func resourcesChanged(_ snapshot: BackgroundResourceSnapshot) async {
        resources = snapshot
        guard active, preferences.learningEnabled, !userPaused else { return }
        if state == .starting && !research.isRunning { return }
        if BackgroundResourcePolicy.shouldPause(snapshot) {
            observedResourcePressure = true
            guard !automaticPause else { return }
            automaticPause = true; state = .pausing
            await research.pause(reason: "resource_preempted")
            if !userPaused { state = .paused }
        } else if automaticPause && observedResourcePressure {
            automaticPause = false
            observedResourcePressure = false
            let token = epoch
            do {
                if research.isRunning { try await research.resume() }
                else { try await startResearch(token: token) }
                if epoch == token && !userPaused { state = .waiting }
            } catch { if epoch == token { state = .blocked(error.localizedDescription) } }
        }
    }

    private func received(_ status: LearningStatus) {
        guard active, !userPaused, preferences.learningEnabled else { return }
        switch status.state {
        case .training: state = automaticPause ? .pausing : .researching
        case .paused:
            if status.reason == "resource_preempted" || status.reason.hasPrefix("resource_preempted:") {
                if !automaticPause { observedResourcePressure = BackgroundResourcePolicy.shouldPause(resources) }
                automaticPause = true
                explanation = (research.resourcePauseReason.map { $0 + ". " } ?? "") +
                    "Research paused for resource capacity. Review resources and retry, or reduce workers. Automatic recovery requires an observed resource improvement."
            }
            state = .paused
        case .pausing: state = .pausing
        case .blocked, .failed: state = .blocked(status.reason)
        case .idle, .waiting, .completed: state = automaticPause ? .paused : .waiting
        }
    }
    private func persist() throws { try store?.save(preferences) }
}
