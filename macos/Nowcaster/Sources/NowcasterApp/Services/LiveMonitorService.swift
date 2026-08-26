import Foundation
import Observation
import CryptoKit
import Darwin

enum LiveMonitorHealthAggregation {
    static func aggregate(_ values: some Sequence<LiveMonitorStatus>) -> LiveMonitorStatus {
        let priority: [LiveMonitorStatus] = [.failed, .stale, .reconnecting, .warming, .healthy, .paused, .stopped]
        return priority.first { values.contains($0) } ?? .warming
    }
}

@MainActor
@Observable
final class LiveMonitorController {
    private(set) var status: LiveMonitorStatus = .stopped
    private(set) var events: [LiveMonitorEvent] = []
    private(set) var activeSetups: [LiveSetup] = []
    private(set) var errorMessage: String?
    private var process: Process?
    @ObservationIgnored private var readerTask: Task<Void, Never>?
    @ObservationIgnored private var diagnosticTask: Task<Void, Never>?
    @ObservationIgnored private var supervisorTask: Task<Void, Never>?
    @ObservationIgnored private let notifications = NotificationService()
    @ObservationIgnored private var inputHandle: FileHandle?
    @ObservationIgnored private var lastEventAt: Date?
    @ObservationIgnored private var restartConfiguration: LiveMonitorConfiguration?
    @ObservationIgnored private var restartCredentials: BrokerCredentials?
    @ObservationIgnored private var restartCount = 0
    @ObservationIgnored private var intentionalStop = false
    @ObservationIgnored private var quietEntryNotifications = false
    @ObservationIgnored private var enabledNotificationCategories = Set(LiveNotificationCategory.allCases)
    @ObservationIgnored private var providerStatuses: [String: LiveMonitorStatus] = [:]
    @ObservationIgnored private let supervisorTimeout: TimeInterval = 45

    var isRunning: Bool { process?.isRunning == true }
    var latestEvent: LiveMonitorEvent? { events.last }

    func requestNotificationPermission() async -> Bool {
        await notifications.requestAuthorization()
    }

    func configureNotifications(quietEntries: Bool, enabledCategories: Set<LiveNotificationCategory>) {
        quietEntryNotifications = quietEntries
        enabledNotificationCategories = enabledCategories
    }

    func start(configuration: LiveMonitorConfiguration, credentials: BrokerCredentials?) async {
        guard !isRunning else { return }
        do {
            let invocation = try configuration.invocation(credentials: credentials)
            guard FileManager.default.isExecutableFile(atPath: invocation.executable.path) else {
                throw EngineRunnerError.invalidExecutable(invocation.executable.path)
            }
            try verifyBundledHelper(invocation.executable)
            let launched = Process()
            let input = Pipe()
            let output = Pipe()
            let diagnostics = Pipe()
            launched.executableURL = invocation.executable
            launched.arguments = invocation.arguments
            launched.currentDirectoryURL = invocation.workingDirectory
            launched.environment = ProcessInfo.processInfo.environment.merging(invocation.environment) { _, new in new }
            launched.standardInput = input
            launched.standardOutput = output
            launched.standardError = diagnostics
            try launched.run()
            try input.fileHandleForWriting.write(contentsOf: invocation.bootstrap)
            inputHandle = input.fileHandleForWriting
            process = launched
            activeSetups = []
            providerStatuses = [:]
            if !configuration.stocks.isEmpty { providerStatuses["alpaca|\(configuration.stockFeed)"] = .warming }
            if !configuration.crypto.isEmpty { providerStatuses["binance|spot"] = .warming }
            restartConfiguration = configuration
            restartCredentials = credentials
            intentionalStop = false
            lastEventAt = .now
            status = .warming
            errorMessage = nil
            diagnosticTask = Task.detached(priority: .utility) {
                while !Task.isCancelled {
                    let data = diagnostics.fileHandleForReading.availableData
                    if data.isEmpty { break }
                    // Diagnostics are intentionally drained but never surfaced verbatim because provider
                    // libraries can include request context. The process exit code is the safe UI boundary.
                }
            }
            readerTask = Task.detached(priority: .userInitiated) { [weak self] in
                var decoder = LiveMonitorEventDecoder()
                do {
                    while true {
                        let data = output.fileHandleForReading.availableData
                        guard !data.isEmpty else { break }
                        for event in try decoder.append(data) { await self?.receive(event) }
                    }
                    launched.waitUntilExit()
                    await self?.didExit(code: launched.terminationStatus)
                } catch {
                    launched.terminate()
                    await self?.didFail(error.localizedDescription)
                }
            }
            supervisorTask = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    guard let self, self.isRunning else { return }
                    if let lastEventAt = self.lastEventAt,
                       Date.now.timeIntervalSince(lastEventAt) > self.supervisorTimeout {
                        self.errorMessage = "The live engine stopped sending health events and will restart."
                        self.process?.terminate()
                        return
                    }
                }
            }
        } catch {
            status = .failed
            errorMessage = error.localizedDescription
        }
    }

    func pause(reason: String = "operator_pause") {
        Task { await shutdownAndWait(reason: reason, graceSeconds: 6) }
    }

    func shutdownForApplicationTermination() async {
        await shutdownAndWait(reason: "app_termination", graceSeconds: 4)
    }

    private func shutdownAndWait(reason _: String, graceSeconds: TimeInterval) async {
        intentionalStop = true
        restartCount = 0
        supervisorTask?.cancel()
        supervisorTask = nil
        try? inputHandle?.write(contentsOf: Data("{\"schema_version\":1,\"command\":\"shutdown\"}\n".utf8))
        try? inputHandle?.close()
        inputHandle = nil
        status = .paused
        guard let running = process, running.isRunning else {
            process = nil
            return
        }
        let pid = running.processIdentifier
        let gracefulDeadline = Date.now.addingTimeInterval(graceSeconds)
        while running.isRunning, Date.now < gracefulDeadline {
            try? await Task.sleep(for: .milliseconds(100))
        }
        if running.isRunning { running.terminate() }
        let terminationDeadline = Date.now.addingTimeInterval(2)
        while running.isRunning, Date.now < terminationDeadline {
            try? await Task.sleep(for: .milliseconds(100))
        }
        if running.isRunning { kill(pid, SIGKILL) }
    }

    private func receive(_ event: LiveMonitorEvent) async {
        guard !events.contains(where: { $0.id == event.id }) else { return }
        events.append(event)
        lastEventAt = .now
        if events.count > 2_000 { events.removeFirst(events.count - 2_000) }
        switch event.type {
        case .ready: status = LiveMonitorHealthAggregation.aggregate(Array(providerStatuses.values))
        case .heartbeat:
            updateProviderHealth(with: event)
        case .providerHealth:
            updateProviderHealth(with: event)
        case .fatalError, .configurationRejected: status = .failed
        default: break
        }
        updateActiveSetups(with: event)
        if event.type == .notificationRequest {
            let category = LiveNotificationCategory(rawValue: event.payload["category"]?.stringValue ?? "") ?? .health
            let delivered = await notifications.deliver(
                LiveNotificationCandidate(
                    id: event.id,
                    category: category,
                    title: event.payload["title"]?.stringValue ?? "Nowcaster Alert",
                    body: event.payload["body"]?.stringValue ?? "Open Nowcaster for details."
                ),
                quietHours: quietEntryNotifications,
                enabledCategories: enabledNotificationCategories
            )
            if delivered {
                sendControl(["schema_version": 1, "command": "notification_delivered", "event_id": event.id])
            }
        }
    }

    private func updateProviderHealth(with event: LiveMonitorEvent) {
        guard let provider = event.payload["provider"]?.stringValue,
              let feed = event.payload["feed"]?.stringValue,
              let raw = event.payload["status"]?.stringValue,
              let value = LiveMonitorStatus(rawValue: raw)
        else { return }
        providerStatuses["\(provider)|\(feed)"] = value
        status = LiveMonitorHealthAggregation.aggregate(Array(providerStatuses.values))
    }

    private func updateActiveSetups(with event: LiveMonitorEvent) {
        if event.type == .setupSnapshot,
           let setup = LiveSetup(payload: event.payload, updatedAt: event.emittedAt) {
            upsert(setup)
            return
        }
        if event.type == .notificationRequest,
           event.payload["category"]?.stringValue == "entry",
           let setup = LiveSetup(payload: event.payload, updatedAt: event.emittedAt) {
            upsert(setup)
            return
        }
        guard event.type == .lifecycleTransition,
              let setupID = event.payload["setup_id"]?.stringValue,
              let target = event.payload["to_state"]?.stringValue
        else { return }
        let terminal = Set(["target_2", "stopped", "closed", "invalidated", "expired"])
        if terminal.contains(target) {
            activeSetups.removeAll { $0.id == setupID }
            return
        }
        guard let index = activeSetups.firstIndex(where: { $0.id == setupID }) else { return }
        activeSetups[index] = activeSetups[index].applying(
            state: target,
            actualFill: event.payload["actual_fill"]?.stringValue,
            reason: event.payload["reason"]?.stringValue ?? target,
            at: event.emittedAt
        )
    }

    private func upsert(_ setup: LiveSetup) {
        if let index = activeSetups.firstIndex(where: { $0.id == setup.id }) {
            activeSetups[index] = setup
        } else {
            activeSetups.append(setup)
        }
        activeSetups.sort { ($0.symbol, $0.id) < ($1.symbol, $1.id) }
    }

    func track(setupID: String, actualFill: Decimal) {
        sendControl([
            "schema_version": 1,
            "command": "track_fill",
            "setup_id": setupID,
            "actual_fill": NSDecimalNumber(decimal: actualFill),
        ])
    }

    private func sendControl(_ value: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(value),
              var data = try? JSONSerialization.data(withJSONObject: value)
        else { return }
        data.append(0x0A)
        try? inputHandle?.write(contentsOf: data)
    }

    private func didExit(code: Int32) {
        supervisorTask?.cancel()
        supervisorTask = nil
        diagnosticTask?.cancel()
        diagnosticTask = nil
        readerTask = nil
        process = nil
        inputHandle = nil
        if intentionalStop || status == .paused { return }
        if restartCount < 2, let restartConfiguration {
            restartCount += 1
            status = .reconnecting
            let credentials = restartCredentials
            let restartDelay = restartCount * 2
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(restartDelay))
                await self?.start(configuration: restartConfiguration, credentials: credentials)
            }
            return
        }
        status = code == 0 ? .stopped : .failed
        if code != 0 { errorMessage = "The live monitor exited with status \(code)." }
    }

    private func didFail(_ message: String) {
        supervisorTask?.cancel()
        diagnosticTask?.cancel()
        diagnosticTask = nil
        process = nil
        status = .failed
        errorMessage = message
    }

    private func verifyBundledHelper(_ executable: URL) throws {
        guard executable.lastPathComponent == "nowcaster-engine" else { return }
        let signature = Process()
        signature.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        signature.arguments = ["--verify", "--strict", executable.path]
        signature.standardOutput = FileHandle.nullDevice
        signature.standardError = FileHandle.nullDevice
        try signature.run()
        signature.waitUntilExit()
        guard signature.terminationStatus == 0 else {
            throw EngineRunnerError.invalidExecutable("bundled helper signature verification failed")
        }
        guard let manifestURL = Bundle.main.url(forResource: "engine-manifest", withExtension: "json"),
              let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL)) as? [String: Any],
              let expected = manifest["executable_sha256"] as? String
        else { throw EngineRunnerError.invalidExecutable("bundled helper manifest is missing") }
        let handle = try FileHandle(forReadingFrom: executable)
        defer { try? handle.close() }
        var digest = SHA256()
        while true {
            let data = try handle.read(upToCount: 1_048_576) ?? Data()
            if data.isEmpty { break }
            digest.update(data: data)
        }
        let observed = digest.finalize().map { String(format: "%02x", $0) }.joined()
        guard observed == expected else {
            throw EngineRunnerError.invalidExecutable("bundled helper manifest verification failed")
        }
    }
}
