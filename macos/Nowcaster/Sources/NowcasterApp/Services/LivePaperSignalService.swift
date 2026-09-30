import AppKit
import Foundation
import Observation

@MainActor protocol PaperResearchNotifying {
    func requestPaperResearchAuthorization() async -> Bool
    func deliverPaperResearch(_ notice: LivePaperNotification, stillAllowed: @MainActor () async -> Bool) async -> Bool
}

struct LivePaperSignalConfiguration: Sendable {
    let projectRoot: URL
    let executable: URL
    let script: URL?
    let directory: URL
    let protocolHash: String

    static func application(directory: URL, protocolHash: String, bundleURL: URL = Bundle.main.bundleURL,
                            sourceRoot: URL, sourcePython: URL) throws -> Self {
        let helper = bundleURL.appending(path: "Contents/Helpers/nowcaster-paper-signals.app/Contents/MacOS/nowcaster-paper-signals")
        if FileManager.default.isExecutableFile(atPath: helper.path) {
            return Self(projectRoot: bundleURL, executable: helper, script: nil,
                        directory: directory, protocolHash: protocolHash)
        }
        // A packaged app must never silently fall back to a source checkout.
        guard bundleURL.pathExtension != "app" else { throw LivePaperServiceError.invalidConfiguration }
        return Self(projectRoot: sourceRoot, executable: sourcePython,
                    script: sourceRoot.appending(path: "scripts/run_live_paper_signals.py"),
                    directory: directory, protocolHash: protocolHash)
    }

    func arguments(for command: String, extra: [String] = []) -> [String] {
        (script.map { ["-u", $0.path] } ?? []) + [command, "--directory", directory.path] + extra
    }

    static var environment: [String: String] {
        var environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"]
        for key in ["HOME", "TMPDIR", "LANG", "LC_ALL"] {
            if let value = ProcessInfo.processInfo.environment[key] { environment[key] = value }
        }
        return environment
    }

    func validate(requireRegistered: Bool = true) throws {
        guard protocolHash.count == 64, protocolHash.allSatisfy({ "0123456789abcdef".contains($0) }),
              FileManager.default.isExecutableFile(atPath: executable.path),
              script.map({ $0.lastPathComponent == "run_live_paper_signals.py" && FileManager.default.fileExists(atPath: $0.path) }) ??
                (executable.lastPathComponent == "nowcaster-paper-signals"),
              !directory.resolvingSymlinksInPath().pathComponents.contains("ProspectiveStudies"),
              !directory.resolvingSymlinksInPath().pathComponents.contains("live-paper-study"),
              !projectRoot.resolvingSymlinksInPath().path.contains("/.worktrees/live-paper-study"),
              !requireRegistered || FileManager.default.fileExists(atPath: directory.appending(path: "protocol.json").path) else {
            throw LivePaperServiceError.invalidConfiguration
        }
    }
}

enum LivePaperServiceError: LocalizedError {
    case invalidConfiguration, commandFailed, outputTooLarge
    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: "Choose a registered research directory and an available paper research engine."
        case .commandFailed: "The paper research service could not complete its request. Review its retained log."
        case .outputTooLarge: "The paper research response exceeded its allowed size."
        }
    }
}

@MainActor @Observable
final class LivePaperSignalService: PaperSessionCollecting {
    static var defaultDirectory: URL {
        AppStorageLocations.root.appending(path: "PaperResearch/paper-desk-v1", directoryHint: .isDirectory)
    }
    private(set) var isRunning = false
    var onTermination: (@MainActor (String) -> Void)?
    private(set) var isBusy = false
    private(set) var notificationsEnabled = false
    private(set) var state: LivePaperSignalState?
    private(set) var events: [LivePaperSignalEvent] = []
    private(set) var message: String?
    private(set) var directory: URL?
    private(set) var providerHealth: ResearchRoundProviderHealth?
    private(set) var decisionEvidence: DayTraderEvidence?
    private(set) var decisionMessage: String?
    @ObservationIgnored private var lastDecisionRead: Date?
    private(set) var notificationEvidence: LivePaperNotificationEvidence?
    private(set) var notificationEvidenceDirectory: URL?
    private(set) var notificationEvidenceMessage: String?
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var processBirth: BackgroundProcessBirth?
    @ObservationIgnored private var launchEpoch = UUID()
    @ObservationIgnored private var startingCommand: BackgroundProcessHandle?
    @ObservationIgnored private var commandProcesses: [UUID: BackgroundProcessHandle] = [:]
    @ObservationIgnored private var commandsAllowed = true
    @ObservationIgnored private var drainCount = 0
    private var draining: Bool { drainCount > 0 }
    @ObservationIgnored private var operations: Set<UUID> = []
    var pendingOperationCount: Int { operations.count }
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var configuration: LivePaperSignalConfiguration?
    @ObservationIgnored private var logHandle: FileHandle?
    @ObservationIgnored private var terminationObserver: (any NSObjectProtocol)?
    @ObservationIgnored private let notifications: any PaperResearchNotifying
    @ObservationIgnored private var authorizationRequest = UUID()
    @ObservationIgnored private var evidenceRequest = UUID()
    @ObservationIgnored private let evidenceStore: PaperNotificationEvidenceStore

    init(notifications: any PaperResearchNotifying = NotificationService(),
         evidenceStore: PaperNotificationEvidenceStore = PaperNotificationEvidenceStore()) {
        self.notifications = notifications
        self.evidenceStore = evidenceStore
    }

    func openNotificationEvidence(materialKey: String, sourceRoot: URL, sourcePython: URL) async {
        guard commandsAllowed else { return }
        let operation = UUID(); operations.insert(operation)
        defer { operations.remove(operation) }
        let request = UUID()
        evidenceRequest = request
        notificationEvidence = nil; notificationEvidenceDirectory = nil
        notificationEvidenceMessage = "Loading retained notification evidence…"
        do {
            let location = try evidenceStore.lookup(materialKey)
            let config = try LivePaperSignalConfiguration.application(directory: location.directory,
                protocolHash: location.protocolHash, sourceRoot: sourceRoot, sourcePython: sourcePython)
            try config.validate()
            let evidence = try await retainedNotification(location, configuration: config)
            guard evidenceRequest == request else { return }
            notificationEvidence = evidence; notificationEvidenceDirectory = location.directory
            notificationEvidenceMessage = nil
        } catch {
            guard evidenceRequest == request else { return }
            notificationEvidenceMessage = "The exact retained notification evidence is unavailable. Its folder may have moved or failed validation."
        }
    }

    func canPresentPaperNotification(materialKey: String) async -> Bool {
        guard commandsAllowed, notificationsEnabled, let config = configuration, let child = process, child.isRunning else { return false }
        let operation = UUID(); operations.insert(operation)
        defer { operations.remove(operation) }
        do {
            let location = try evidenceStore.lookup(materialKey)
            guard location.protocolHash == config.protocolHash,
                  location.directory.resolvingSymlinksInPath() == config.directory.resolvingSymlinksInPath() else { return false }
            let evidence = try await retainedNotification(location, configuration: config)
            let data = try await ownedCommand(config, "status")
            let current = try LivePaperSignalState.decode(data, protocolHash: config.protocolHash, now: Date())
            return notificationsEnabled && !Task.isCancelled && process === child && child.isRunning
                && configuration?.directory == config.directory && configuration?.protocolHash == config.protocolHash
                && current.currentSuggestion(now: Date(), isRunning: isRunning) == evidence.suggestion
                && evidence.notification.isFresh(at: Date())
        } catch { return false }
    }

    private func retainedNotification(_ location: PaperNotificationLocation,
        configuration: LivePaperSignalConfiguration) async throws -> LivePaperNotificationEvidence {
        let data = try await ownedCommand(configuration, "notification-evidence", extra: ["--protocol-hash",
            location.protocolHash, "--material-key", location.materialKey])
        return try LivePaperNotificationEvidence.decode(data, location: location)
    }

    func open(directory: URL, sourceRoot: URL, sourcePython: URL) async {
        guard !isRunning, !isBusy, !draining else { return }
        commandsAllowed = true
        let epoch = launchEpoch
        isBusy = true
        defer { isBusy = false }
        do {
            if let root = AppStorageLocations.acceptanceRoot,
               !directory.resolvingSymlinksInPath().path.hasPrefix(root.path + "/") {
                throw BackgroundResearchError.invalidPath
            }
            let provisional = try LivePaperSignalConfiguration.application(directory: directory,
                protocolHash: String(repeating: "0", count: 64), sourceRoot: sourceRoot, sourcePython: sourcePython)
            try provisional.validate()
            let data = try await ownedCommand(provisional, "status")
            guard epoch == launchEpoch else { return }
            guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let identity = root["protocol_hash"] as? String else { throw LivePaperServiceError.invalidConfiguration }
            let current = try LivePaperSignalState.decode(data, protocolHash: identity, now: Date())
            let selected = try LivePaperSignalConfiguration.application(directory: directory,
                protocolHash: current.protocolHash, sourceRoot: sourceRoot, sourcePython: sourcePython)
            let history = try Self.readHistory(directory)
            let health = try Self.readProviderHealth(directory, protocolHash: current.protocolHash)
            configuration = selected
            self.directory = directory
            state = current
            events = history; providerHealth = health
            decisionEvidence = nil; decisionMessage = nil; lastDecisionRead = nil
            if let configuration { await readDecisionEvidence(configuration) }
            guard epoch == launchEpoch, commandsAllowed else { return }
            message = nil
        } catch { if epoch == launchEpoch { message = error.localizedDescription } }
    }

    func startSelected() async {
        guard let configuration else { return }
        await start(configuration: configuration)
    }

    func createOrResumeDesk(sourceRoot: URL, sourcePython: URL) async {
        guard !isRunning, !isBusy, !draining else { return }
        commandsAllowed = true
        let epoch = launchEpoch
        isBusy = true
        let directory = Self.defaultDirectory
        do {
            let config = try LivePaperSignalConfiguration.application(directory: directory,
                protocolHash: String(repeating: "0", count: 64), sourceRoot: sourceRoot, sourcePython: sourcePython)
            try config.validate(requireRegistered: false)
            _ = try await ownedCommand(config, "setup")
            guard epoch == launchEpoch else { isBusy = false; return }
            isBusy = false
            await open(directory: directory, sourceRoot: sourceRoot, sourcePython: sourcePython)
        } catch { isBusy = false; message = error.localizedDescription }
    }

    func importCalendar(_ file: URL) async {
        guard let configuration, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try configuration.validate()
            _ = try await ownedCommand(configuration, "import-calendar", extra: ["--file", file.path])
            message = "Calendar evidence retained. Coverage, age and blackout checks still apply."
        } catch { message = "Calendar not imported. Use a current, covered calendar JSON in the documented format; old or conflicting evidence is rejected." }
    }

    func start(configuration: LivePaperSignalConfiguration) async {
        guard !isRunning, !isBusy, !draining else { return }
        commandsAllowed = true
        isBusy = true
        let epoch = UUID(); launchEpoch = epoch
        let commandOwner = BackgroundProcessHandle(); startingCommand = commandOwner
        defer { isBusy = false }
        state = nil; events = []; providerHealth = nil; message = nil
        decisionEvidence = nil; decisionMessage = nil; lastDecisionRead = nil
        do {
            try configuration.validate()
            let initial = try await Self.command(configuration, "status", owner: commandOwner)
            guard launchEpoch == epoch, !Task.isCancelled else { return }
            startingCommand = nil
            _ = try LivePaperSignalState.decode(initial, protocolHash: configuration.protocolHash, now: Date())
            let logURL = configuration.directory.appending(path: "paper-signal-app.log")
            if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
            let log = try FileHandle(forWritingTo: logURL)
            try log.seekToEnd()
            let child = Process()
            child.executableURL = configuration.executable
            child.arguments = configuration.arguments(for: "start")
            child.currentDirectoryURL = configuration.projectRoot
            child.environment = LivePaperSignalConfiguration.environment
            child.standardOutput = log; child.standardError = log
            child.terminationHandler = { [weak self] terminated in
                let status = terminated.terminationStatus
                Task { @MainActor [weak self] in
                    guard let self, self.process === terminated else { return }
                    self.finish(status: status)
                }
            }
            try child.run()
            process = child; logHandle = log; self.configuration = configuration
            processBirth = BackgroundProcessBirth.read(child.processIdentifier)
            directory = configuration.directory; isRunning = child.isRunning
            terminationObserver = NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification,
                object: nil, queue: .main) { _ in if child.isRunning { child.terminate() } }
            monitor = Task { [weak self] in
                while !Task.isCancelled {
                    guard let self, self.isRunning else { return }
                    await self.refresh()
                    try? await Task.sleep(for: .seconds(1))
                }
            }
        } catch { message = error.localizedDescription }
    }

    func stop() async {
        _ = await shutdown(timeout: .seconds(30))
    }

    var selectedSource: PaperSessionSource? {
        guard let configuration else { return nil }
        return .init(directory: configuration.directory, protocolHash: configuration.protocolHash)
    }
    var collectionHealthy: Bool {
        guard isRunning, let state, message == nil else { return false }
        return ["warming", "abstaining", "published"].contains(state.kind) && Date().timeIntervalSince(state.updatedAt) < 15
    }
    func selectSource(_ source: PaperSessionSource, configuration: EngineConfiguration) async throws {
        if let selectedSource, selectedSource != source { throw BackgroundResearchError.identityMismatch }
        if !isRunning {
            await open(directory: source.directory, sourceRoot: configuration.projectRoot, sourcePython: configuration.pythonExecutable)
        }
        guard selectedSource == source else { throw BackgroundResearchError.identityMismatch }
    }
    func chooseSource(directory: URL, configuration: EngineConfiguration, setup: Bool) async throws -> PaperSessionSource {
        guard !isRunning, !isBusy, !draining else { throw LivePaperServiceError.commandFailed }
        if setup { await createOrResumeDesk(sourceRoot: configuration.projectRoot, sourcePython: configuration.pythonExecutable) }
        else { await open(directory: directory, sourceRoot: configuration.projectRoot, sourcePython: configuration.pythonExecutable) }
        guard message == nil, let source = selectedSource, source.directory == directory else {
            throw LivePaperServiceError.commandFailed
        }
        return source
    }
    func startCollection() async throws {
        guard configuration != nil else { throw BackgroundResearchError.missingRegistration }
        await startSelected()
        guard isRunning else { throw LivePaperServiceError.commandFailed }
    }
    func shutdown(timeout: Duration) async -> Bool {
        drainCount += 1
        defer { drainCount -= 1 }
        commandsAllowed = false
        authorizationRequest = UUID(); evidenceRequest = UUID(); notificationsEnabled = false
        launchEpoch = UUID(); monitor?.cancel(); monitor = nil
        isRunning = false; state = nil
        let clock = ContinuousClock(), deadline = ContinuousClock.now + min(max(timeout, .zero), .seconds(30))
        let pending = startingCommand; pending?.requestStop()
        let commands = Array(commandProcesses.values)
        for command in commands { command.requestStop() }
        let commandDrain = Task {
            await withTaskGroup(of: Bool.self) { group in
                for command in commands { group.addTask { await command.shutdown(timeout: max(.zero, clock.now.duration(to: deadline))) } }
                var result = true
                for await stopped in group { result = result && stopped }
                return result
            }
        }
        // A launched contender may not hold the directory lock. Never send the
        // directory-wide stop command: it could stop an unrelated lock holder.
        guard let child = process, child.isRunning else {
            let stopped = await pending?.shutdown(timeout: max(.zero, clock.now.duration(to: deadline))) ?? true
            startingCommand = nil
            let commandsStopped = await commandDrain.value
            let settled = await settleOperations(until: deadline)
            return commandsStopped && stopped && settled
        }
        let birth = processBirth
        func stillOwned() -> Bool {
            child.isRunning && birth != nil && BackgroundProcessBirth.read(child.processIdentifier) == birth
        }
        // The backend catches KeyboardInterrupt only inside its acquired lock.
        // Before acquisition SIGINT exits without writing shared stop control.
        if stillOwned() { child.interrupt() }
        while child.isRunning, clock.now < deadline { try? await Task.sleep(for: .milliseconds(50)) }
        let stopped = !child.isRunning
        if stillOwned() { child.terminate(); if stillOwned() { kill(child.processIdentifier, SIGKILL) } }
        _ = await pending?.shutdown(timeout: .zero)
        startingCommand = nil
        message = stopped ? "Paper collection stopped locally. Retained backend state has not been verified."
            : "Paper collection was interrupted. Retained backend state has not been verified."
        let commandsStopped = await commandDrain.value
        let settled = await settleOperations(until: deadline)
        return commandsStopped && stopped && settled
    }

    private func settleOperations(until deadline: ContinuousClock.Instant) async -> Bool {
        while !operations.isEmpty, ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(10))
        }
        return operations.isEmpty
    }

    func setNotificationsEnabled(_ enabled: Bool) async {
        let request = UUID()
        authorizationRequest = request
        notificationsEnabled = false
        guard enabled, commandsAllowed else { return }
        let operation = UUID(); operations.insert(operation)
        defer { operations.remove(operation) }
        let allowed = await notifications.requestPaperResearchAuthorization()
        guard authorizationRequest == request else { return }
        notificationsEnabled = allowed
        if !allowed { message = "Paper research notifications are disabled in macOS notification settings." }
    }

    func refresh() async {
        guard commandsAllowed, let configuration, process?.isRunning == true else { state = nil; return }
        let epoch = launchEpoch, operation = UUID(); operations.insert(operation)
        defer { operations.remove(operation) }
        do {
            let data = try await ownedCommand(configuration, "status")
            guard commandsAllowed, !Task.isCancelled, process?.isRunning == true else { return }
            state = try LivePaperSignalState.decode(data, protocolHash: configuration.protocolHash, now: Date())
            events = try Self.readHistory(configuration.directory)
            providerHealth = try Self.readProviderHealth(configuration.directory, protocolHash: configuration.protocolHash)
            if lastDecisionRead.map({ Date().timeIntervalSince($0) >= 5 }) ?? true {
                await readDecisionEvidence(configuration)
            }
            guard epoch == launchEpoch, commandsAllowed, !Task.isCancelled else { return }
            message = nil
            if notificationsEnabled, state?.currentSuggestion(now: Date(), isRunning: isRunning) != nil {
                await notify(configuration)
            }
        } catch { if epoch == launchEpoch, commandsAllowed { state = nil; providerHealth = nil; decisionEvidence = nil; message = error.localizedDescription } }
    }

    private func readDecisionEvidence(_ configuration: LivePaperSignalConfiguration) async {
        let epoch = launchEpoch
        lastDecisionRead = Date()
        do {
            let data = try await ownedCommand(configuration, "decision-context")
            let evidence = try DayTraderEvidence.decode(data, protocolHash: configuration.protocolHash, now: Date())
            guard self.configuration?.directory == configuration.directory, !Task.isCancelled else { return }
            decisionEvidence = evidence; decisionMessage = nil
        } catch {
            guard commandsAllowed, launchEpoch == epoch else { return }
            decisionEvidence = nil
            decisionMessage = "Decision context is unavailable. Retained evidence could not be validated."
        }
    }

    private func notify(_ configuration: LivePaperSignalConfiguration) async {
        let epoch = launchEpoch
        guard commandsAllowed, !Task.isCancelled else { return }
        do {
            let reservedSuggestion = state?.suggestion
            let data = try await ownedCommand(configuration, "notification", extra: ["--enabled", "--protocol-hash", configuration.protocolHash])
            if String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == "null" { return }
            let notice = try LivePaperNotification.decodeReservation(data, protocolHash: configuration.protocolHash)
            // Permission requests, process calls and delivery each cross time boundaries.
            // Re-read current evidence after reservation and recheck at actual scheduling.
            var delivered = false
            do {
                let latest = try await ownedCommand(configuration, "status")
                let status = try LivePaperSignalState.decode(latest, protocolHash: configuration.protocolHash, now: Date())
                let accepted = notificationsEnabled && !Task.isCancelled && process?.isRunning == true
                    && status.suggestion == reservedSuggestion && notice.isFresh(at: Date())
                    && status.suggestion?.candidateHash == notice.candidateHash
                    && status.suggestion?.symbol == notice.symbol && status.suggestion?.expiresAt == notice.expiresAt
                    && status.currentSuggestion(now: Date(), isRunning: isRunning) != nil
                if accepted {
                    try evidenceStore.record(notice, directory: configuration.directory)
                    delivered = await notifications.deliverPaperResearch(notice) { [weak self] in
                        guard let self, self.commandsAllowed, self.launchEpoch == epoch, !Task.isCancelled else { return false }
                        do {
                            // The monitor is suspended while macOS checks notification
                            // settings. Its cached state cannot authorize scheduling.
                            let currentData = try await self.ownedCommand(configuration, "status")
                            let current = try LivePaperSignalState.decode(currentData,
                                protocolHash: configuration.protocolHash, now: Date())
                            self.state = current
                            return self.notificationsEnabled && !Task.isCancelled && self.process?.isRunning == true
                                && current.suggestion == reservedSuggestion
                                && current.currentSuggestion(now: Date(), isRunning: self.isRunning) != nil
                                && notice.isFresh(at: Date())
                        } catch {
                            guard self.launchEpoch == epoch, self.commandsAllowed else { return false }
                            self.state = nil
                            self.message = "Paper notification withheld: \(error.localizedDescription)"
                            return false
                        }
                    }
                }
            } catch { if launchEpoch == epoch, commandsAllowed { message = "Paper notification withheld: \(error.localizedDescription)" } }
            guard launchEpoch == epoch, commandsAllowed, !Task.isCancelled else { return }
            _ = try await ownedCommand(configuration, "notification-outcome", extra: ["--protocol-hash", configuration.protocolHash,
                "--material-key", notice.materialKey, "--outcome", delivered ? "delivered" : "failed"])
        } catch { if launchEpoch == epoch, commandsAllowed { message = "Paper notification withheld: \(error.localizedDescription)" } }
    }

    private func finish(status: Int32) {
        let unexpected = commandsAllowed
        commandsAllowed = false; launchEpoch = UUID()
        authorizationRequest = UUID(); evidenceRequest = UUID(); notificationsEnabled = false
        for command in commandProcesses.values { command.requestStop() }
        isRunning = false; state = nil
        monitor?.cancel(); monitor = nil
        try? logHandle?.close(); logHandle = nil
        process = nil
        processBirth = nil
        if let terminationObserver { NotificationCenter.default.removeObserver(terminationObserver) }
        terminationObserver = nil
        if status != 0 { message = "The paper research service stopped. Review paper-signal-app.log in the research directory." }
        if unexpected { onTermination?(message ?? "Paper collection ended. Start to retry; retained evidence is unchanged.") }
    }

    private static func readHistory(_ directory: URL) throws -> [LivePaperSignalEvent] {
        let path = directory.appending(path: "signal-events.jsonl")
        guard FileManager.default.fileExists(atPath: path.path) else { return [] }
        let handle = try FileHandle(forReadingFrom: path)
        defer { try? handle.close() }
        let length = try handle.seekToEnd()
        let start = length > 262_144 ? length - 262_144 : 0
        try handle.seek(toOffset: start)
        var data = try handle.read(upToCount: 262_144) ?? Data()
        if start > 0 {
            guard let newline = data.firstIndex(of: 10) else { throw LivePaperServiceError.outputTooLarge }
            data = data.subdata(in: data.index(after: newline) ..< data.endIndex)
        }
        return Array(try LivePaperSignalEvent.decodeHistory(data, now: Date()).suffix(200))
    }

    nonisolated static func readProviderHealth(_ directory: URL, protocolHash: String) throws -> ResearchRoundProviderHealth? {
        let liveURL = directory.appending(path: "live-paper-provider-health.json")
        let live = FileManager.default.fileExists(atPath: liveURL.path)
        let url = live ? liveURL : directory.appending(path: "research-round-2-summary.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let data = try handle.read(upToCount: 1_048_577) ?? Data()
        guard data.count <= 1_048_576 else { throw LivePaperServiceError.outputTooLarge }
        if live {
            guard case let .object(root) = try JSONDecoder.nowcaster.decode(JSONValue.self, from: data),
                  Set(root.keys) == ["protocolHash", "providerHealth"],
                  case let .string(identity) = root["protocolHash"], identity == protocolHash,
                  let health = root["providerHealth"] else { throw LivePaperServiceError.invalidConfiguration }
            return try ResearchRoundProviderHealth(value: health)
        }
        let report = try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self, from: data)
        guard report.protocolHash == protocolHash else { throw LivePaperServiceError.invalidConfiguration }
        return report.providerHealth
    }

    nonisolated private static func command(_ configuration: LivePaperSignalConfiguration, _ command: String, extra: [String] = [], owner: BackgroundProcessHandle) async throws -> Data {
        try await Task.detached {
            let child = Process(), pipe = Pipe()
            child.executableURL = configuration.executable
            child.arguments = configuration.arguments(for: command, extra: extra)
            child.currentDirectoryURL = configuration.projectRoot
            child.environment = LivePaperSignalConfiguration.environment
            child.standardOutput = pipe; child.standardError = FileHandle.nullDevice
            try owner.launch(child)
            let timeout = DispatchWorkItem { if child.isRunning { child.terminate() } }
            DispatchQueue.global().asyncAfter(deadline: .now() + 15, execute: timeout)
            defer { timeout.cancel(); try? pipe.fileHandleForReading.close() }
            var output = Data()
            while let chunk = try pipe.fileHandleForReading.read(upToCount: 8192), !chunk.isEmpty {
                output.append(chunk)
                if output.count > 65_536 { child.terminate(); throw LivePaperServiceError.outputTooLarge }
            }
            child.waitUntilExit()
            guard child.terminationStatus == 0 else { throw LivePaperServiceError.commandFailed }
            return output
        }.value
    }

    private func ownedCommand(_ configuration: LivePaperSignalConfiguration, _ command: String, extra: [String] = []) async throws -> Data {
        guard commandsAllowed, !Task.isCancelled else { throw CancellationError() }
        let epoch = launchEpoch
        let id = UUID(), owner = BackgroundProcessHandle()
        commandProcesses[id] = owner
        defer { commandProcesses[id] = nil }
        let data = try await Self.command(configuration, command, extra: extra, owner: owner)
        guard commandsAllowed, launchEpoch == epoch, !Task.isCancelled else { throw CancellationError() }
        return data
    }
}
