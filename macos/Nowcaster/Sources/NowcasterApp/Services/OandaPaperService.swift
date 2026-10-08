import Foundation
import Observation

struct OandaPaperServiceConfiguration: Sendable {
    let executable: URL
    let script: URL?
    let directory: URL

    static func application(bundleURL: URL = Bundle.main.bundleURL,
                            directory: URL = AppStorageLocations.root.appending(path: "IntradayResearch")) -> Self {
        let helper = bundleURL.appending(path: "Contents/Helpers/nowcaster-oanda-paper.app/Contents/MacOS/nowcaster-oanda-paper")
        return .init(executable: helper, script: nil, directory: directory)
    }

    var arguments: [String] {
        (script.map { ["-u", $0.path] } ?? []) + ["run", "--directory", directory.path]
    }

    func validate() throws {
        let resolved = directory.resolvingSymlinksInPath()
        guard FileManager.default.isExecutableFile(atPath: executable.path),
              !resolved.pathComponents.contains("ProspectiveStudies"),
              !resolved.pathComponents.contains("live-paper-study"),
              executable.lastPathComponent == "nowcaster-oanda-paper" ||
                (script?.lastPathComponent == "intraday_service_entry.py" && executable.lastPathComponent == "python")
        else { throw OandaPaperServiceError.invalidConfiguration }
    }
}

enum OandaPaperServiceError: LocalizedError {
    case invalidConfiguration
    case credentialsUnavailable
    case launchFailed

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: "The bundled OANDA practice helper is unavailable. Rebuild Nowcaster."
        case .credentialsUnavailable: "Save an OANDA practice token in Keychain before starting paper monitoring."
        case .launchFailed: "The practice feed did not start. Check the retained research log."
        }
    }
}

@MainActor @Observable
final class OandaPaperService {
    private(set) var isRunning = false
    private(set) var message: String?
    private(set) var directory: URL
    @ObservationIgnored private let vault: OandaPracticeCredentialVault
    @ObservationIgnored private let configuration: OandaPaperServiceConfiguration
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var logHandle: FileHandle?

    init(vault: OandaPracticeCredentialVault = .init(),
         configuration: OandaPaperServiceConfiguration = .application()) {
        self.vault = vault
        self.configuration = configuration
        directory = configuration.directory
    }

    func start() {
        guard !isRunning else { return }
        do {
            try configuration.validate()
            guard let credentials = try vault.load() else { throw OandaPaperServiceError.credentialsUnavailable }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let pause = directory.appending(path: "pause.request")
            if FileManager.default.fileExists(atPath: pause.path) { try FileManager.default.removeItem(at: pause) }
            let logURL = directory.appending(path: "collector.log")
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: Data())
            }
            let log = try FileHandle(forWritingTo: logURL)
            try log.seekToEnd()
            let child = Process()
            child.executableURL = configuration.executable
            child.arguments = configuration.arguments
            var environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1"]
            for key in ["HOME", "TMPDIR", "LANG", "LC_ALL"] {
                if let value = ProcessInfo.processInfo.environment[key] { environment[key] = value }
            }
            environment["OANDA_PRACTICE_ACCOUNT_ID"] = credentials.accountID
            environment["OANDA_PRACTICE_TOKEN"] = credentials.token
            child.environment = environment
            child.standardOutput = log
            child.standardError = log
            child.terminationHandler = { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.isRunning = false
                    self?.process = nil
                    try? self?.logHandle?.close()
                    self?.logHandle = nil
                    self?.message = "Practice feed stopped; displayed quotes may be stale."
                }
            }
            try child.run()
            logHandle = log
            process = child
            isRunning = true
            message = "Practice feed running. Setups remain experimental; no broker orders are sent."
        } catch {
            message = error.localizedDescription
        }
    }

    func pause() {
        guard let process, process.isRunning else { isRunning = false; return }
        do {
            try Data("pause\n".utf8).write(to: directory.appending(path: "pause.request"), options: .atomic)
            message = "Pausing after the next feed event."
        } catch { message = "Pause request could not be saved; practice feed is still running." }
    }

    func shutdown() {
        pause()
        // This helper currently makes no paper entries. Future managed
        // positions must be drained before enabling forced shutdown here.
        process?.terminate()
    }
}
