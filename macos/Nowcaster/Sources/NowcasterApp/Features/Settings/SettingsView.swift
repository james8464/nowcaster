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
    }

    @ObservationIgnored private let defaults: UserDefaults

    var projectRootPath: String { didSet { defaults.set(projectRootPath, forKey: Key.projectRoot) } }
    var pythonExecutablePath: String { didSet { defaults.set(pythonExecutablePath, forKey: Key.pythonExecutable) } }
    var snapshotPath: String { didSet { defaults.set(snapshotPath, forKey: Key.snapshot) } }
    var mode: EngineMode { didSet { defaults.set(mode.rawValue, forKey: Key.mode) } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let root = defaults.string(forKey: Key.projectRoot) ?? FileManager.default.currentDirectoryPath
        projectRootPath = root
        pythonExecutablePath = defaults.string(forKey: Key.pythonExecutable) ?? "\(root)/.venv/bin/python"
        snapshotPath = defaults.string(forKey: Key.snapshot) ?? "\(root)/data/app/nowcaster-snapshot.json"
        mode = EngineMode(rawValue: defaults.string(forKey: Key.mode) ?? "demo") ?? .demo
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
        .frame(width: 640, height: 460)
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
