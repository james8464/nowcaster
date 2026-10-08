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

enum OandaPaperNotificationGate {
    static func canNotifyClose(_ status: IntradayDeskStatus, brokerSymbol: String, at now: Date) -> Bool {
        status.isFresh(at: now) && status.feedHealth == "healthy" &&
            status.markets.contains { $0.brokerSymbol == brokerSymbol && $0.eligibility == "paper_eligible" }
    }
    static func eligibleSetupIDs(_ status: IntradayDeskStatus, at now: Date) -> Set<String> {
        guard status.isFresh(at: now), status.feedHealth == "healthy" else { return [] }
        let eligible = Set(status.markets.filter { $0.eligibility == "paper_eligible" }.compactMap(\.brokerSymbol))
        return Set(status.opportunities.compactMap { idea in
            guard eligible.contains(idea.brokerSymbol), let key = idea.evidenceHash,
                  key.count == 64, key.allSatisfy({ "0123456789abcdef".contains($0) }) else { return nil }
            return key
        })
    }
}

@MainActor @Observable
final class OandaPaperService {
    private(set) var isRunning = false
    private(set) var notificationsEnabled: Bool
    private(set) var message: String?
    private(set) var directory: URL
    @ObservationIgnored private let vault: OandaPracticeCredentialVault
    @ObservationIgnored private let configuration: OandaPaperServiceConfiguration
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var logHandle: FileHandle?
    @ObservationIgnored private var notificationTask: Task<Void, Never>?
    @ObservationIgnored private let notifications = NotificationService()
    @ObservationIgnored private var observedOpportunityIDs: Set<String> = []
    @ObservationIgnored private var observedPositionIDs: Set<String> = []
    @ObservationIgnored private var observedClosedCount: Int?

    init(vault: OandaPracticeCredentialVault = .init(),
         configuration: OandaPaperServiceConfiguration = .application()) {
        self.vault = vault
        self.configuration = configuration
        directory = configuration.directory
        notificationsEnabled = AppStorageLocations.defaults.bool(forKey: "oandaPaperNotifications")
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
                    self?.notificationTask?.cancel()
                    self?.notificationTask = nil
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
            establishNotificationBaseline()
            observeNotifications()
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
        notificationTask?.cancel()
        notificationTask = nil
        // This helper currently makes no paper entries. Future managed
        // positions must be drained before enabling forced shutdown here.
        process?.terminate()
    }

    func setNotificationsEnabled(_ enabled: Bool) async {
        if enabled {
            guard await notifications.requestAuthorization() else {
                message = "macOS notifications were not authorized. Paper alerts remain off."
                return
            }
        }
        notificationsEnabled = enabled
        AppStorageLocations.defaults.set(enabled, forKey: "oandaPaperNotifications")
        observedOpportunityIDs.removeAll()
        observedPositionIDs.removeAll()
        observedClosedCount = nil
        establishNotificationBaseline()
    }

    private func establishNotificationBaseline() {
        if let data = try? Data(contentsOf: directory.appending(path: "summary.json"), options: .mappedIfSafe),
           let status = try? IntradayDeskStatus.decode(data) {
            observedOpportunityIDs.formUnion(status.opportunities.compactMap(\.evidenceHash))
            observedPositionIDs.formUnion(status.paperPositions.map { $0.brokerSymbol + ":" + $0.openedAt })
        }
        if let data = try? Data(contentsOf: directory.appending(path: "report.json"), options: .mappedIfSafe),
           let report = try? IntradayPaperReport.decode(data) {
            observedClosedCount = report.closedTrades
        }
    }

    private func observeNotifications() {
        notificationTask?.cancel()
        notificationTask = Task { [weak self] in
            while let self, !Task.isCancelled, self.isRunning {
                await self.checkFreshPaperEvents()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func checkFreshPaperEvents() async {
        guard notificationsEnabled else { return }
        let now = Date()
        let statusURL = directory.appending(path: "summary.json")
        let status = (try? Data(contentsOf: statusURL, options: .mappedIfSafe)).flatMap { try? IntradayDeskStatus.decode($0) }
        if let status, status.isFresh(at: now),
           status.feedHealth == "healthy" {
            let eligible = Set(status.markets.filter { $0.eligibility == "paper_eligible" }.compactMap(\.brokerSymbol))
            for key in OandaPaperNotificationGate.eligibleSetupIDs(status, at: now) {
                guard !observedOpportunityIDs.contains(key) else { continue }
                observedOpportunityIDs.insert(key)
                _ = await notifications.deliver(.init(id: "oanda-paper-setup-" + key, category: .entry,
                                                      title: "Experimental paper setup", body: "Review the full ticket in Nowcaster."))
            }
            for position in status.paperPositions where eligible.contains(position.brokerSymbol) {
                let key = position.brokerSymbol + ":" + position.openedAt
                guard !observedPositionIDs.contains(key) else { continue }
                observedPositionIDs.insert(key)
                _ = await notifications.deliver(.init(id: "oanda-paper-open-" + key, category: .entry,
                                                      title: "Paper position opened", body: "Review the simulated position in Nowcaster."))
            }
        }
        let reportURL = directory.appending(path: "report.json")
        if let data = try? Data(contentsOf: reportURL, options: .mappedIfSafe),
           let report = try? IntradayPaperReport.decode(data), report.isFresh(at: now) {
            if let previous = observedClosedCount, report.closedTrades > previous,
               let last = report.closedRecords.last, let status,
               OandaPaperNotificationGate.canNotifyClose(status, brokerSymbol: last.brokerSymbol, at: now) {
                _ = await notifications.deliver(.init(id: "oanda-paper-close-" + String(report.closedTrades),
                                                      category: .close, title: "Paper position closed",
                                                      body: "Review the retained outcome and costs in Nowcaster."))
            }
            observedClosedCount = report.closedTrades
        }
    }
}
