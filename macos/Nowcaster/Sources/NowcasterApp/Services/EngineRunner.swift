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
        return AsyncThrowingStream { continuation in
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

                let invocation = job.invocation(configuration: configuration)
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
                continuation.yield(EngineProgressEvent(event: "job_started", stage: job.rawValue, progress: 0))
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let text = String(decoding: data, as: UTF8.self)
                let lines = text.split(whereSeparator: \Character.isNewline).map(String.init)
                for line in lines.suffix(200) {
                    if let event = try? EngineProgressEvent.parse(line) {
                        continuation.yield(event)
                    } else {
                        continuation.yield(
                            EngineProgressEvent(event: "diagnostic", stage: job.rawValue, message: String(line.prefix(4_000)))
                        )
                    }
                }
                guard process.terminationStatus == 0 else {
                    let diagnostics = lines.suffix(20).joined(separator: "\n")
                    continuation.finish(
                        throwing: EngineRunnerError.nonzeroExit(process.terminationStatus, diagnostics: diagnostics)
                    )
                    return
                }
                continuation.yield(EngineProgressEvent(event: "job_completed", stage: job.rawValue, progress: 1))
                continuation.finish()
            }
        }
    }
}
