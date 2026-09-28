import Darwin
import Foundation
import Observation

enum BackgroundResearchEnvironment {
    static func make(from inherited: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
        let allowed: Set<String> = ["HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT"]
        var result = inherited.filter { allowed.contains($0.key) }
        for key in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"] { result[key] = "1" }
        return result
    }
}

struct BackgroundProcessBirth: Equatable, Sendable {
    let parentPID: Int32
    let seconds: UInt64
    let microseconds: UInt64

    static func read(_ pid: Int32) -> Self? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
        return .init(parentPID: Int32(info.pbi_ppid), seconds: info.pbi_start_tvsec, microseconds: info.pbi_start_tvusec)
    }
}

/// Only this launched Process (and a verified direct bootloader child) can be owned.
final class BackgroundProcessHandle: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var launchBirth: BackgroundProcessBirth?
    private var ownership: BackgroundProcessOwnership?
    private var stopped = false
    let request: BackgroundResearchRequest?
    init(request: BackgroundResearchRequest? = nil) { self.request = request }

    func launch(_ process: Process) throws {
        try lock.withLock {
            guard !stopped else { throw CancellationError() }
            try process.run()
            self.process = process
            launchBirth = BackgroundProcessBirth.read(process.processIdentifier)
        }
    }
    var isRunning: Bool { lock.withLock { process?.isRunning == true } }
    var processID: Int32? { lock.withLock { process?.processIdentifier } }
    var hasOwnership: Bool { lock.withLock { ownership != nil } }
    var stopRequested: Bool { lock.withLock { stopped } }

    func accept(_ receipt: BackgroundProcessOwnership) throws {
        try lock.withLock {
            guard let request, receipt.schemaVersion == 1,
                  receipt.runID == request.runID, receipt.nonce == request.controlNonce,
                  receipt.campaignHash == request.campaignHash, receipt.pid > 1,
                  receipt.processGroupID == receipt.pid, let process,
                  process.isRunning, let launchBirth,
                  BackgroundProcessBirth.read(process.processIdentifier) == launchBirth,
                  let birth = BackgroundProcessBirth.read(receipt.pid),
                  birth.parentPID == receipt.parentPID,
                  (receipt.pid == process.processIdentifier || receipt.parentPID == process.processIdentifier),
                  birth.seconds == receipt.processStartSeconds, birth.microseconds == receipt.processStartMicroseconds,
                  getpgid(receipt.pid) == receipt.pid else { throw BackgroundResearchError.ownershipMismatch }
            if let ownership, ownership != receipt { throw BackgroundResearchError.ownershipMismatch }
            ownership = receipt
        }
    }

    func requestStop() {
        lock.withLock { stopped = true }
        if let request { try? request.control.request(.stopped) }
    }

    /// Called only at the shutdown deadline. Re-read all OS ownership before signaling.
    @discardableResult func signalOwned(_ signal: Int32) -> Bool {
        lock.withLock {
            guard let process, process.isRunning, let launchBirth,
                  BackgroundProcessBirth.read(process.processIdentifier) == launchBirth else { return false }
            if let owner = ownership, let request,
               owner.runID == request.runID, owner.nonce == request.controlNonce, owner.campaignHash == request.campaignHash,
               let birth = BackgroundProcessBirth.read(owner.pid), birth.parentPID == owner.parentPID,
               (owner.pid == process.processIdentifier || owner.parentPID == process.processIdentifier),
               birth.seconds == owner.processStartSeconds, birth.microseconds == owner.processStartMicroseconds,
               getpgid(owner.pid) == owner.pid {
                return kill(-owner.pid, signal) == 0
            }
            // Pre-handshake: native ownership permits only our Process, never a guessed group.
            return kill(process.processIdentifier, signal) == 0
        }
    }

    func shutdown(timeout: Duration, clock: BackgroundShutdownClock = .continuous) async -> Bool {
        requestStop()
        let bounded = min(max(timeout, .zero), .seconds(30))
        let deadline = clock.now() + bounded
        while isRunning && clock.now() < deadline {
            await clock.sleep(min(.milliseconds(50), deadline - clock.now()))
        }
        guard isRunning else { return true }
        _ = signalOwned(SIGTERM)
        _ = signalOwned(SIGKILL)
        return false
    }
}

struct BackgroundShutdownClock: Sendable {
    let now: @Sendable () -> Duration
    let sleep: @Sendable (Duration) async -> Void
    static var continuous: Self {
        let origin = ContinuousClock.now
        return .init(now: { origin.duration(to: .now) }, sleep: { try? await Task.sleep(for: $0) })
    }
}

@MainActor @Observable final class BackgroundResearchService: PaperSessionResearching {
    private(set) var status: LearningStatus?
    private(set) var isRunning = false
    private(set) var isPreparing = false
    private(set) var isStarting = false
    private(set) var hasVerifiedOwnership = false
    private(set) var interrupted = false
    private(set) var message: String?
    private(set) var resourcePauseReason: String?
    private(set) var request: BackgroundResearchRequest?
    var onStatus: (@MainActor (LearningStatus) -> Void)?
    var onFailure: (@MainActor (String) -> Void)?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var commandTask: Task<[EngineProgressEvent], Error>?
    @ObservationIgnored private var owner: BackgroundProcessHandle?
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var workers = BackgroundResourcePolicy.efficientWorkers(ProcessInfo.processInfo.activeProcessorCount)
    @ObservationIgnored private var acknowledged = false

    func prepare(preferences: PaperSessionPreferences, configuration: EngineConfiguration) async throws -> PaperSessionPreferences {
        guard !isRunning, !isPreparing else { throw BackgroundResearchError.missingRegistration }
        guard let source = preferences.source, let manifest = preferences.manifestURL,
              let registry = preferences.registryURL, let id = preferences.campaignID, let created = preferences.createdAt else { throw BackgroundResearchError.missingRegistration }
        var result = preferences
        workers = preferences.resourceProfile.workers(cores: ProcessInfo.processInfo.activeProcessorCount)
        isPreparing = true
        let token = UUID(); generation = token
        defer { if generation == token { isPreparing = false; owner = nil; commandTask = nil } }
        let prepared = try await command(.prepareBackgroundResearch(.init(source: source, manifestURL: manifest, campaignID: id, seed: preferences.seed, createdAt: created)), configuration: configuration)
        guard generation == token, let event = prepared.first(where: { $0.event == "prepared" }),
              event.schemaVersion == 1, event.sourceProtocolHash == source.protocolHash else { throw BackgroundResearchError.identityMismatch }
        let registered = try await command(.registerBackgroundResearch(.init(registryURL: registry, manifestURL: manifest,
            expectedCampaignHash: preferences.campaignHash, expectedRuntimeCodeIdentity: preferences.runtimeCodeIdentity)), configuration: configuration)
        guard generation == token, let event = registered.first(where: { $0.event == "registered" }), event.schemaVersion == 1,
              let hash = event.campaignHash, LearningStatus.isDigest(hash), let runtime = event.runtimeCodeIdentity, LearningStatus.isDigest(runtime),
              event.status?.campaignHash == hash,
              preferences.campaignHash.map({ $0 == hash }) ?? true,
              preferences.runtimeCodeIdentity.map({ $0 == runtime }) ?? true else { throw BackgroundResearchError.identityMismatch }
        result.campaignHash = hash; result.runtimeCodeIdentity = runtime; status = event.status
        return result
    }

    private func command(_ job: EngineJob, configuration: EngineConfiguration) async throws -> [EngineProgressEvent] {
        let handle = BackgroundProcessHandle(); owner = handle
        let operation = Task {
            var events: [EngineProgressEvent] = []
            for try await event in EngineRunner(backgroundOwner: handle).run(job, configuration: configuration) {
                try Task.checkCancellation()
                events.append(event)
            }
            return events
        }
        commandTask = operation
        return try await operation.value
    }

    func start(campaignHash: String, registryURL: URL, configuration: EngineConfiguration) async throws {
        guard !isRunning, !isPreparing else { return }
        let resolved = registryURL.resolvingSymlinksInPath()
        guard resolved.path != "/", resolved != FileManager.default.homeDirectoryForCurrentUser,
              Set(resolved.pathComponents).isDisjoint(with: ["ProspectiveStudies", "live-paper-study"]) else { throw BackgroundResearchError.invalidPath }
        var ancestor = resolved
        while ancestor.path != "/" {
            guard !FileManager.default.fileExists(atPath: ancestor.appending(path: "protocol.json").path) else { throw BackgroundResearchError.invalidPath }
            ancestor.deleteLastPathComponent()
        }
        let token = UUID(); generation = token
        let execution = BackgroundResearchRequest(registryURL: registryURL, campaignHash: campaignHash, runID: UUID().uuidString,
            controlDirectory: registryURL.appending(path: "controls"), controlNonce: UUID().uuidString + UUID().uuidString, workers: workers)
        _ = try EngineJob.backgroundResearch(execution).invocation(configuration: configuration)
        guard !FileManager.default.fileExists(atPath: execution.control.identity.fileURL.path) else { throw BackgroundResearchError.identityMismatch }
        try execution.control.initialize()
        request = execution; acknowledged = false; interrupted = false; message = nil
        isStarting = true; hasVerifiedOwnership = false
        resourcePauseReason = nil
        let handle = BackgroundProcessHandle(request: execution); owner = handle; isRunning = true
        task = Task { [weak self] in
            do {
                for try await event in EngineRunner(backgroundOwner: handle).run(.backgroundResearch(execution), configuration: configuration) {
                    guard let self, self.generation == token else { continue }
                    if let receipt = event.ownership {
                        try handle.accept(receipt)
                        self.hasVerifiedOwnership = true; self.isStarting = false
                    }
                    if event.stage == "resource_guard" || event.stage == "resource_preemption" { self.resourcePauseReason = event.message }
                    if event.event == "diagnostic" { throw BackgroundResearchError.invalidStatus }
                    if let status = event.status {
                        guard handle.hasOwnership, event.schemaVersion == 1, status.campaignHash == execution.campaignHash else { throw BackgroundResearchError.identityMismatch }
                        self.status = status; self.onStatus?(status)
                    }
                    if event.event == "complete" { self.acknowledged = handle.hasOwnership && event.status != nil }
                }
                guard let self, self.generation == token else { return }
                self.isRunning = false; self.isStarting = false
                if !self.acknowledged {
                    self.interrupted = true; self.message = BackgroundResearchError.interrupted.localizedDescription
                    self.onFailure?(BackgroundResearchError.interrupted.localizedDescription)
                }
            } catch {
                let cancelledBeforeLaunch = error is CancellationError && handle.processID == nil && handle.stopRequested
                handle.requestStop()
                _ = await handle.shutdown(timeout: .seconds(30))
                guard let self, self.generation == token else { return }
                self.isRunning = false; self.isStarting = false
                if !cancelledBeforeLaunch {
                    self.interrupted = true; self.message = error.localizedDescription
                    self.onFailure?(error.localizedDescription)
                }
            }
        }
    }

    func pause(reason: String) async {
        guard let request, isRunning else { return }
        do { try request.control.request(.paused); message = reason }
        catch { message = error.localizedDescription }
    }
    func resume() async throws {
        guard isRunning, let request else { throw BackgroundResearchError.missingRegistration }
        try request.control.request(.running); message = nil
    }

    func shutdown(timeout: Duration) async -> Bool {
        let handle = owner
        let preparing = isPreparing
        let token = generation
        let deadline = ContinuousClock.now + min(max(timeout, .zero), .seconds(30))
        // Invalidate preparation now so its late reply cannot launch registration.
        if preparing { generation = UUID() }
        // Request durable STOP before cancelling any stream. Preparation has no checkpoint protocol.
        handle?.requestStop()
        let launched = handle?.processID != nil
        if preparing { commandTask?.cancel() }
        let stopped = await handle?.shutdown(timeout: timeout) ?? true
        if stopped && !preparing && launched {
            while isRunning, generation == token, ContinuousClock.now < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        }
        if !stopped || (!preparing && launched && isRunning) {
            interrupted = true; message = BackgroundResearchError.interrupted.localizedDescription
            if let request {
                let receipt: [String: String] = ["run_id": request.runID, "campaign_hash": request.campaignHash, "reason": "shutdown_deadline_interrupted"]
                if let data = try? JSONEncoder().encode(receipt) { try? data.write(to: request.controlDirectory.appending(path: "\(request.runID).interrupted.json"), options: .atomic) }
            }
        }
        generation = UUID(); commandTask?.cancel(); commandTask = nil
        task?.cancel(); task = nil; isPreparing = false; isRunning = false; isStarting = false; hasVerifiedOwnership = false; owner = nil
        return stopped && !interrupted
    }
}
