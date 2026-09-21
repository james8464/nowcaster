import AppKit
import Foundation
import Observation

@MainActor protocol PaperResearchNotifying {
    func requestPaperResearchAuthorization() async -> Bool
    func deliverPaperResearch(_ notice: LivePaperNotification, stillAllowed: @MainActor () -> Bool) async -> Bool
}

struct LivePaperSignalConfiguration: Sendable {
    let projectRoot: URL
    let executable: URL
    let script: URL?
    let directory: URL
    let protocolHash: String

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

    func validate() throws {
        guard protocolHash.count == 64, protocolHash.allSatisfy({ "0123456789abcdef".contains($0) }),
              FileManager.default.isExecutableFile(atPath: executable.path),
              script.map({ $0.lastPathComponent == "run_live_paper_signals.py" && FileManager.default.fileExists(atPath: $0.path) }) ??
                (executable.lastPathComponent == "nowcaster-paper-signals"),
              !directory.resolvingSymlinksInPath().path.contains("/ProspectiveStudies/"),
              !projectRoot.resolvingSymlinksInPath().path.contains("/.worktrees/live-paper-study"),
              FileManager.default.fileExists(atPath: directory.appending(path: "protocol.json").path) else {
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
final class LivePaperSignalService {
    private(set) var isRunning = false
    private(set) var isBusy = false
    private(set) var notificationsEnabled = false
    private(set) var state: LivePaperSignalState?
    private(set) var events: [LivePaperSignalEvent] = []
    private(set) var message: String?
    private(set) var directory: URL?
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var configuration: LivePaperSignalConfiguration?
    @ObservationIgnored private var logHandle: FileHandle?
    @ObservationIgnored private var terminationObserver: (any NSObjectProtocol)?
    @ObservationIgnored private let notifications: any PaperResearchNotifying
    @ObservationIgnored private var authorizationRequest = UUID()

    init(notifications: any PaperResearchNotifying = NotificationService()) {
        self.notifications = notifications
    }

    func start(configuration: LivePaperSignalConfiguration) async {
        guard !isRunning, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        state = nil; events = []; message = nil
        do {
            try configuration.validate()
            let initial = try await Self.command(configuration, "status")
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
        guard let configuration, let child = process, !isBusy else { return }
        isBusy = true
        monitor?.cancel(); monitor = nil
        state = nil
        do { _ = try await Self.command(configuration, "stop") }
        catch { message = error.localizedDescription }
        for _ in 0 ..< 30 {
            if !child.isRunning { break }
            try? await Task.sleep(for: .milliseconds(500))
        }
        if child.isRunning { child.terminate() }
        isRunning = child.isRunning
        isBusy = false
    }

    func setNotificationsEnabled(_ enabled: Bool) async {
        let request = UUID()
        authorizationRequest = request
        notificationsEnabled = false
        guard enabled else { return }
        let allowed = await notifications.requestPaperResearchAuthorization()
        guard authorizationRequest == request else { return }
        notificationsEnabled = allowed
        if !allowed { message = "Paper research notifications are disabled in macOS notification settings." }
    }

    func refresh() async {
        guard let configuration, process?.isRunning == true else { state = nil; return }
        do {
            let data = try await Self.command(configuration, "status")
            guard !Task.isCancelled, process?.isRunning == true else { return }
            state = try LivePaperSignalState.decode(data, protocolHash: configuration.protocolHash, now: Date())
            events = try Self.readHistory(configuration.directory)
            message = nil
            if notificationsEnabled, state?.currentSuggestion(now: Date(), isRunning: isRunning) != nil {
                await notify(configuration)
            }
        } catch { state = nil; message = error.localizedDescription }
    }

    private func notify(_ configuration: LivePaperSignalConfiguration) async {
        do {
            let reservedSuggestion = state?.suggestion
            let data = try await Self.command(configuration, "notification", extra: ["--enabled", "--protocol-hash", configuration.protocolHash])
            if String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == "null" { return }
            let notice = try LivePaperNotification.decodeReservation(data, protocolHash: configuration.protocolHash)
            // Permission requests, process calls and delivery each cross time boundaries.
            // Re-read current evidence after reservation and recheck at actual scheduling.
            var delivered = false
            do {
                let latest = try await Self.command(configuration, "status")
                let status = try LivePaperSignalState.decode(latest, protocolHash: configuration.protocolHash, now: Date())
                let accepted = notificationsEnabled && !Task.isCancelled && process?.isRunning == true
                    && status.suggestion == reservedSuggestion && notice.isFresh(at: Date())
                    && status.suggestion?.candidateHash == notice.candidateHash
                    && status.suggestion?.symbol == notice.symbol && status.suggestion?.expiresAt == notice.expiresAt
                    && status.currentSuggestion(now: Date(), isRunning: isRunning) != nil
                if accepted {
                    delivered = await notifications.deliverPaperResearch(notice) { [weak self] in
                        guard let self else { return false }
                        return self.notificationsEnabled && !Task.isCancelled && self.process?.isRunning == true
                            && self.state?.suggestion == reservedSuggestion
                    }
                }
            } catch { message = "Paper notification withheld: \(error.localizedDescription)" }
            _ = try await Self.command(configuration, "notification-outcome", extra: ["--protocol-hash", configuration.protocolHash,
                "--material-key", notice.materialKey, "--outcome", delivered ? "delivered" : "failed"])
        } catch { message = "Paper notification withheld: \(error.localizedDescription)" }
    }

    private func finish(status: Int32) {
        isRunning = false; state = nil
        monitor?.cancel(); monitor = nil
        try? logHandle?.close(); logHandle = nil
        process = nil
        if let terminationObserver { NotificationCenter.default.removeObserver(terminationObserver) }
        terminationObserver = nil
        if status != 0 { message = "The paper research service stopped. Review paper-signal-app.log in the research directory." }
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

    nonisolated static func command(_ configuration: LivePaperSignalConfiguration, _ command: String, extra: [String] = []) async throws -> Data {
        try await Task.detached {
            let child = Process(), pipe = Pipe()
            child.executableURL = configuration.executable
            child.arguments = configuration.arguments(for: command, extra: extra)
            child.currentDirectoryURL = configuration.projectRoot
            child.environment = LivePaperSignalConfiguration.environment
            child.standardOutput = pipe; child.standardError = FileHandle.nullDevice
            try child.run()
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
}
