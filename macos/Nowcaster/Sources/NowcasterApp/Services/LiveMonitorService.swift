import Foundation
import Observation

@MainActor
@Observable
final class LiveMonitorController {
    private(set) var status: LiveMonitorStatus = .stopped
    private(set) var events: [LiveMonitorEvent] = []
    private(set) var errorMessage: String?
    private var process: Process?
    @ObservationIgnored private var readerTask: Task<Void, Never>?
    @ObservationIgnored private let notifications = NotificationService()

    var isRunning: Bool { process?.isRunning == true }
    var latestEvent: LiveMonitorEvent? { events.last }

    func requestNotificationPermission() async -> Bool {
        await notifications.requestAuthorization()
    }

    func start(configuration: LiveMonitorConfiguration, credentials: BrokerCredentials?) async {
        guard !isRunning else { return }
        do {
            let invocation = try configuration.invocation(credentials: credentials)
            guard FileManager.default.isExecutableFile(atPath: invocation.executable.path) else {
                throw EngineRunnerError.invalidExecutable(invocation.executable.path)
            }
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
            try input.fileHandleForWriting.close()
            process = launched
            status = .warming
            errorMessage = nil
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
        } catch {
            status = .failed
            errorMessage = error.localizedDescription
        }
    }

    func pause(reason _: String = "operator_pause") {
        readerTask?.cancel()
        readerTask = nil
        if process?.isRunning == true { process?.terminate() }
        process = nil
        status = .paused
    }

    private func receive(_ event: LiveMonitorEvent) async {
        guard !events.contains(where: { $0.id == event.id }) else { return }
        events.append(event)
        if events.count > 2_000 { events.removeFirst(events.count - 2_000) }
        switch event.type {
        case .ready: status = .warming
        case .providerHealth:
            if let raw = event.payload["status"]?.stringValue, let value = LiveMonitorStatus(rawValue: raw) {
                status = value
            }
        case .fatalError, .configurationRejected: status = .failed
        default: break
        }
        if event.type == .notificationRequest {
            let category = LiveNotificationCategory(rawValue: event.payload["category"]?.stringValue ?? "") ?? .health
            await notifications.deliver(
                LiveNotificationCandidate(
                    id: event.id,
                    category: category,
                    title: event.payload["title"]?.stringValue ?? "Nowcaster Alert",
                    body: event.payload["body"]?.stringValue ?? "Open Nowcaster for details."
                )
            )
        }
    }

    private func didExit(code: Int32) {
        process = nil
        if status != .paused { status = code == 0 ? .stopped : .failed }
        if code != 0 { errorMessage = "The live monitor exited with status \(code)." }
    }

    private func didFail(_ message: String) {
        process = nil
        status = .failed
        errorMessage = message
    }
}
