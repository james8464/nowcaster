import Foundation

enum EngineMode: String, Codable, CaseIterable, Sendable {
    case demo
    case live
}

struct EngineConfiguration: Sendable {
    let projectRoot: URL
    let pythonExecutable: URL
    let snapshotURL: URL
    let mode: EngineMode
}

struct EngineInvocation: Sendable {
    let executableURL: URL
    let arguments: [String]
    let workingDirectoryURL: URL
}

enum EngineJob: String, CaseIterable, Sendable {
    case rebuildAll
    case fullBacktest
    case exportSnapshot

    var title: String {
        switch self {
        case .rebuildAll: "Rebuild all research"
        case .fullBacktest: "Run full backtest"
        case .exportSnapshot: "Export app snapshot"
        }
    }

    func invocation(configuration: EngineConfiguration) -> EngineInvocation {
        let command: [String]
        switch self {
        case .rebuildAll:
            command = ["demo"]
        case .fullBacktest:
            command = ["run-all"]
        case .exportSnapshot:
            command = ["export-app-snapshot", "--output", configuration.snapshotURL.path]
        }
        return EngineInvocation(
            executableURL: configuration.pythonExecutable,
            arguments: ["-m", "src.cli"] + command + [
                "--mode", configuration.mode.rawValue,
                "--project-root", configuration.projectRoot.path,
            ],
            workingDirectoryURL: configuration.projectRoot
        )
    }
}

struct EngineProgressEvent: Codable, Equatable, Sendable, Identifiable {
    let id: UUID
    let event: String
    let stage: String?
    let progress: Double?
    let message: String?

    private enum CodingKeys: String, CodingKey {
        case event, stage, progress, message
    }

    init(event: String, stage: String? = nil, progress: Double? = nil, message: String? = nil) {
        id = UUID()
        self.event = event
        self.stage = stage
        self.progress = progress
        self.message = message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = UUID()
        event = try container.decode(String.self, forKey: .event)
        stage = try container.decodeIfPresent(String.self, forKey: .stage)
        progress = try container.decodeIfPresent(Double.self, forKey: .progress)
        message = try container.decodeIfPresent(String.self, forKey: .message)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(event, forKey: .event)
        try container.encodeIfPresent(stage, forKey: .stage)
        try container.encodeIfPresent(progress, forKey: .progress)
        try container.encodeIfPresent(message, forKey: .message)
    }

    static func parse(_ line: String) throws -> EngineProgressEvent {
        try JSONDecoder().decode(EngineProgressEvent.self, from: Data(line.utf8))
    }
}
