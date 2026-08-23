import Foundation

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
    private(set) var diagnostics: [String] = []
    private(set) var emittedDiagnostics: [String] = []

    init(maximumDiagnostics: Int = 20, maximumLineBytes: Int = 64 * 1_024) {
        self.maximumDiagnostics = max(maximumDiagnostics, 1)
        self.maximumLineBytes = max(maximumLineBytes, 1_024)
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
        let line = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .newlines)
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

    func set(_ process: Process) {
        lock.withLock { self.process = process }
    }

    func terminate() {
        lock.withLock {
            guard let process, process.isRunning else { return }
            process.terminate()
        }
    }
}

struct EngineRunner: Sendable {
    func run(
        _ job: EngineJob,
        configuration: EngineConfiguration
    ) -> AsyncThrowingStream<EngineProgressEvent, Error> {
        let holder = RunningProcess()
        return AsyncThrowingStream(bufferingPolicy: .bufferingNewest(256)) { continuation in
            continuation.onTermination = { _ in holder.terminate() }
            Task.detached(priority: .userInitiated) {
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
                process.environment = ProcessInfo.processInfo.environment.merging(["PYTHONUNBUFFERED": "1"]) { _, new in new }
                holder.set(process)
                do {
                    try process.run()
                } catch {
                    continuation.finish(throwing: EngineRunnerError.launchFailed(error.localizedDescription))
                    return
                }

                continuation.yield(EngineProgressEvent(event: "job_started", stage: job.stageName, progress: 0))
                var decoder = EngineOutputDecoder()
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
                        if Task.isCancelled {
                            holder.terminate()
                            throw CancellationError()
                        }
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
        }
    }
}
