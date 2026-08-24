import Foundation
import Darwin

protocol EngineRunning: Sendable {
    func run(
        _ job: EngineJob,
        configuration: EngineConfiguration
    ) -> AsyncThrowingStream<EngineProgressEvent, Error>
}

enum EngineRunnerError: Error, Equatable, LocalizedError, Sendable {
    case invalidProjectRoot(String)
    case invalidExecutable(String)
    case launchFailed(String)
    case nonzeroExit(Int32, diagnostics: String)

    var errorDescription: String? {
        switch self {
        case let .invalidProjectRoot(path): "Project root is unavailable: \(path)"
        case let .invalidExecutable(path): "Python executable is unavailable: \(path)"
        case let .launchFailed(message): "Could not start the research engine: \(message)"
        case let .nonzeroExit(status, diagnostics):
            "Research engine exited with status \(status). \(diagnostics)"
        }
    }
}

struct EngineOutputDecoder: Sendable {
    private var buffer = Data()
    private let maximumDiagnostics: Int
    private let maximumLineBytes: Int
    private let redactedValues: [String]
    private(set) var diagnostics: [String] = []
    private(set) var emittedDiagnostics: [String] = []

    init(maximumDiagnostics: Int = 20, maximumLineBytes: Int = 64 * 1_024, redactedValues: [String] = []) {
        self.maximumDiagnostics = max(maximumDiagnostics, 1)
        self.maximumLineBytes = max(maximumLineBytes, 1_024)
        self.redactedValues = redactedValues.filter { !$0.isEmpty }
    }

    mutating func append(_ data: Data) -> [EngineProgressEvent] {
        emittedDiagnostics = []
        buffer.append(data)
        var events: [EngineProgressEvent] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer[..<newline]
            buffer.removeSubrange(...newline)
            if let event = process(Data(lineData)) { events.append(event) }
        }
        if buffer.count > maximumLineBytes {
            recordDiagnostic(String(decoding: buffer.prefix(maximumLineBytes), as: UTF8.self))
            buffer.removeAll(keepingCapacity: true)
        }
        return events
    }

    mutating func finish() -> [EngineProgressEvent] {
        emittedDiagnostics = []
        guard !buffer.isEmpty else { return [] }
        let remainder = buffer
        buffer.removeAll(keepingCapacity: false)
        return process(remainder).map { [$0] } ?? []
    }

    private mutating func process(_ data: Data) -> EngineProgressEvent? {
        var line = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .newlines)
        for value in redactedValues { line = line.replacingOccurrences(of: value, with: "[REDACTED]") }
        guard !line.isEmpty else { return nil }
        if let event = try? EngineProgressEvent.parse(line) { return event }
        recordDiagnostic(line)
        return nil
    }

    private mutating func recordDiagnostic(_ line: String) {
        let bounded = String(line.prefix(4_000))
        emittedDiagnostics.append(bounded)
        if emittedDiagnostics.count > maximumDiagnostics {
            emittedDiagnostics.removeFirst(emittedDiagnostics.count - maximumDiagnostics)
        }
        diagnostics.append(bounded)
        if diagnostics.count > maximumDiagnostics {
            diagnostics.removeFirst(diagnostics.count - maximumDiagnostics)
        }
    }
}

private final class RunningProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var worker: Task<Void, Never>?
    private var terminationRequested = false
    private let terminationGrace: Duration = .milliseconds(250)

    func setWorker(_ worker: Task<Void, Never>) {
        let cancel = lock.withLock {
            self.worker = worker
            return terminationRequested
        }
        if cancel { worker.cancel() }
    }

    func clearWorker() {
        lock.withLock { worker = nil }
    }

    func checkCancellation() throws {
        let requested = lock.withLock { terminationRequested }
        if requested || Task.isCancelled { throw CancellationError() }
    }

    func launch(_ process: Process) throws {
        lock.lock()
        defer { lock.unlock() }
        guard !terminationRequested, !Task.isCancelled else { throw CancellationError() }
        self.process = process
        do {
            try process.run()
        } catch {
            self.process = nil
            throw error
        }
        if terminationRequested || Task.isCancelled {
            requestTermination(process)
            throw CancellationError()
        }
    }

    func terminate() {
        let active = lock.withLock { () -> (Task<Void, Never>?, Process?) in
            terminationRequested = true
            return (worker, process)
        }
        active.0?.cancel()
        if let process = active.1 { requestTermination(process) }
    }

    private func requestTermination(_ process: Process) {
        guard process.isRunning else { return }
        process.terminate()
        let pid = process.processIdentifier
        let grace = terminationGrace
        Task.detached(priority: .utility) {
            try? await Task.sleep(for: grace)
            if process.isRunning {
                _ = Darwin.kill(pid, SIGKILL)
            }
        }
    }
}

struct EngineRunner: EngineRunning, Sendable {
    private let beforeLaunch: @Sendable () async -> Void

    init(beforeLaunch: @escaping @Sendable () async -> Void = {}) {
        self.beforeLaunch = beforeLaunch
    }

    func run(
        _ job: EngineJob,
        configuration: EngineConfiguration
    ) -> AsyncThrowingStream<EngineProgressEvent, Error> {
        let holder = RunningProcess()
        return AsyncThrowingStream(bufferingPolicy: .bufferingNewest(256)) { continuation in
            continuation.onTermination = { _ in holder.terminate() }
            let worker = Task.detached(priority: .userInitiated) {
                defer { holder.clearWorker() }
                do {
                    try holder.checkCancellation()
                } catch {
                    continuation.finish(throwing: error)
                    return
                }
                var isDirectory: ObjCBool = false
                guard FileManager.default.fileExists(
                    atPath: configuration.projectRoot.path,
                    isDirectory: &isDirectory
                ), isDirectory.boolValue else {
                    continuation.finish(throwing: EngineRunnerError.invalidProjectRoot(configuration.projectRoot.path))
                    return
                }
                guard FileManager.default.isExecutableFile(atPath: configuration.pythonExecutable.path) else {
                    continuation.finish(throwing: EngineRunnerError.invalidExecutable(configuration.pythonExecutable.path))
                    return
                }

                let invocation: EngineInvocation
                do {
                    invocation = try job.invocation(configuration: configuration)
                } catch {
                    continuation.finish(throwing: error)
                    return
                }
                let process = Process()
                let output = Pipe()
                process.executableURL = invocation.executableURL
                process.arguments = invocation.arguments
                process.currentDirectoryURL = invocation.workingDirectoryURL
                process.standardOutput = output
                process.standardError = output
                let brokerEnvironment = configuration.secretEnvironment?.consume() ?? [:]
                process.environment = ProcessInfo.processInfo.environment
                    .merging(["PYTHONUNBUFFERED": "1"]) { _, new in new }
                    .merging(invocation.environment) { _, new in new }
                    .merging(brokerEnvironment) { _, new in new }
                await beforeLaunch()
                do {
                    try holder.checkCancellation()
                    try holder.launch(process)
                    try holder.checkCancellation()
                } catch {
                    if error is CancellationError {
                        continuation.finish(throwing: error)
                    } else {
                        continuation.finish(throwing: EngineRunnerError.launchFailed(error.localizedDescription))
                    }
                    return
                }

                continuation.yield(EngineProgressEvent(event: "job_started", stage: job.stageName, progress: 0))
                var decoder = EngineOutputDecoder(
                    redactedValues: Array(brokerEnvironment.values) + Array(invocation.environment.values)
                )
                do {
                    while true {
                        let data = output.fileHandleForReading.availableData
                        guard !data.isEmpty else { break }
                        for event in decoder.append(data) { continuation.yield(event) }
                        for diagnostic in decoder.emittedDiagnostics {
                            continuation.yield(
                                EngineProgressEvent(event: "diagnostic", stage: job.stageName, message: diagnostic)
                            )
                        }
                        try holder.checkCancellation()
                    }
                    for event in decoder.finish() { continuation.yield(event) }
                    for diagnostic in decoder.emittedDiagnostics {
                        continuation.yield(
                            EngineProgressEvent(event: "diagnostic", stage: job.stageName, message: diagnostic)
                        )
                    }
                } catch {
                    holder.terminate()
                    continuation.finish(throwing: error)
                    return
                }
                process.waitUntilExit()
                guard process.terminationStatus == 0 else {
                    continuation.finish(
                        throwing: EngineRunnerError.nonzeroExit(
                            process.terminationStatus,
                            diagnostics: decoder.diagnostics.joined(separator: "\n")
                        )
                    )
                    return
                }
                continuation.yield(EngineProgressEvent(event: "job_completed", stage: job.stageName, progress: 1))
                continuation.finish()
            }
            holder.setWorker(worker)
        }
    }
}
