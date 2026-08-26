import AppKit
import Observation
import SwiftUI

struct SettingsValidationIssue: Identifiable, Sendable {
    let field: String
    let message: String

    var id: String { field }
}

@MainActor
@Observable
final class AppSettings {
    private enum Key {
        static let projectRoot = "projectRootPath"
        static let pythonExecutable = "pythonExecutablePath"
        static let snapshot = "snapshotPath"
        static let mode = "engineMode"
        static let stockWatchlist = "monitorStockWatchlist"
        static let cryptoWatchlist = "monitorCryptoWatchlist"
        static let monitorAtLogin = "monitorAtLogin"
        static let resumeMonitoring = "resumeMonitoringAtLogin"
    }

    @ObservationIgnored private let defaults: UserDefaults

    var projectRootPath: String { didSet { defaults.set(projectRootPath, forKey: Key.projectRoot) } }
    var pythonExecutablePath: String { didSet { defaults.set(pythonExecutablePath, forKey: Key.pythonExecutable) } }
    var snapshotPath: String { didSet { defaults.set(snapshotPath, forKey: Key.snapshot) } }
    var mode: EngineMode { didSet { defaults.set(mode.rawValue, forKey: Key.mode) } }
    var stockWatchlist: String { didSet { defaults.set(stockWatchlist, forKey: Key.stockWatchlist) } }
    var cryptoWatchlist: String { didSet { defaults.set(cryptoWatchlist, forKey: Key.cryptoWatchlist) } }
    var monitorAtLogin: Bool { didSet { defaults.set(monitorAtLogin, forKey: Key.monitorAtLogin) } }
    var resumeMonitoring: Bool { didSet { defaults.set(resumeMonitoring, forKey: Key.resumeMonitoring) } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let root = defaults.string(forKey: Key.projectRoot) ?? FileManager.default.currentDirectoryPath
        projectRootPath = root
        pythonExecutablePath = defaults.string(forKey: Key.pythonExecutable) ?? "\(root)/.venv/bin/python"
        snapshotPath = defaults.string(forKey: Key.snapshot) ?? "\(root)/data/app/nowcaster-snapshot.json"
        mode = EngineMode(rawValue: defaults.string(forKey: Key.mode) ?? "demo") ?? .demo
        stockWatchlist = defaults.string(forKey: Key.stockWatchlist) ?? "AAPL, SPY"
        cryptoWatchlist = defaults.string(forKey: Key.cryptoWatchlist) ?? "BTCUSDT, ETHUSDT"
        monitorAtLogin = defaults.bool(forKey: Key.monitorAtLogin)
        resumeMonitoring = defaults.bool(forKey: Key.resumeMonitoring)
    }

    var normalizedStocks: [String] { normalizeWatchlist(stockWatchlist) }
    var normalizedCrypto: [String] { normalizeWatchlist(cryptoWatchlist) }

    private func normalizeWatchlist(_ value: String) -> [String] {
        Array(Set(value.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() }
            .filter { !$0.isEmpty })).sorted()
    }

    var configuration: EngineConfiguration {
        EngineConfiguration(
            projectRoot: URL(fileURLWithPath: projectRootPath),
            pythonExecutable: URL(fileURLWithPath: pythonExecutablePath),
            snapshotURL: URL(fileURLWithPath: snapshotPath),
            mode: mode
        )
    }

    func validationIssues() -> [SettingsValidationIssue] {
        var issues: [SettingsValidationIssue] = []
        var isDirectory: ObjCBool = false
        if !FileManager.default.fileExists(atPath: projectRootPath, isDirectory: &isDirectory) || !isDirectory.boolValue {
            issues.append(SettingsValidationIssue(field: "Project root", message: "Choose the repository folder."))
        }
        if !FileManager.default.isExecutableFile(atPath: pythonExecutablePath) {
            issues.append(SettingsValidationIssue(field: "Python", message: "Choose an executable Python runtime."))
        }
        if !FileManager.default.fileExists(atPath: snapshotPath) {
            issues.append(SettingsValidationIssue(field: "Snapshot", message: "Build or choose a snapshot JSON file."))
        }
        return issues
    }
}

struct SettingsView: View {
    @Bindable var settings: AppSettings
    @State private var loginItemMessage: String?

    var body: some View {
        Form {
            Section("Research engine") {
                pathRow("Project root", text: $settings.projectRootPath, chooseDirectories: true)
                pathRow("Python executable", text: $settings.pythonExecutablePath)
                Picker("Data mode", selection: $settings.mode) {
                    ForEach(EngineMode.allCases, id: \.self) { mode in
                        Text(mode.rawValue.capitalized).tag(mode)
                    }
                }
            }
            Section("Native snapshot") {
                pathRow("Snapshot JSON", text: $settings.snapshotPath)
            }
            Section("Live Monitor") {
                TextField("Stocks", text: $settings.stockWatchlist, prompt: Text("AAPL, SPY"))
                TextField("Crypto", text: $settings.cryptoWatchlist, prompt: Text("BTCUSDT, ETHUSDT"))
                Toggle("Start Nowcaster at login", isOn: $settings.monitorAtLogin)
                    .onChange(of: settings.monitorAtLogin) { _, enabled in
                        do {
                            try LoginItemService.setEnabled(enabled)
                            loginItemMessage = LoginItemService.statusDescription
                        } catch {
                            settings.monitorAtLogin = false
                            loginItemMessage = error.localizedDescription
                        }
                    }
                Toggle("Resume monitoring at login", isOn: $settings.resumeMonitoring)
                    .disabled(!settings.monitorAtLogin)
                if let loginItemMessage { Text(loginItemMessage).font(.caption).foregroundStyle(.secondary) }
                Text("Comma-separated watchlists. Monitoring is notification-only and stops while this Mac sleeps or is offline.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            BrokerCredentialsView(vault: BrokerCredentialVault())
            let issues = settings.validationIssues()
            if !issues.isEmpty {
                Section("Configuration health") {
                    ForEach(issues) { issue in
                        Label {
                            VStack(alignment: .leading) {
                                Text(issue.field)
                                Text(issue.message).font(.caption).foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        }
                    }
                }
            }
            Section {
                Text("Nowcaster stores only these local paths and the selected mode in preferences. Broker credentials use Keychain.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .frame(width: 640, height: 620)
    }

    private func pathRow(_ title: String, text: Binding<String>, chooseDirectories: Bool = false) -> some View {
        LabeledContent(title) {
            HStack {
                TextField(title, text: text)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel(title)
                Button("Choose…") {
                    let panel = NSOpenPanel()
                    panel.canChooseDirectories = chooseDirectories
                    panel.canChooseFiles = !chooseDirectories
                    panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK, let url = panel.url {
                        text.wrappedValue = url.path
                    }
                }
            }
        }
    }
}
