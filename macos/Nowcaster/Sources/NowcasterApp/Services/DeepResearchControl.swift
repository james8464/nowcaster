import Foundation

enum DeepResearchControlState: String, Codable, Sendable {
    case running
    case paused
    case stopped
}

enum DeepResearchThermalAction: Equatable, Sendable {
    case none
    case pause
    case resume
}

enum DeepResearchThermalPolicy {
    static func action(
        for state: ProcessInfo.ThermalState,
        automaticallyPaused: Bool
    ) -> DeepResearchThermalAction {
        switch state {
        case .serious, .critical:
            return automaticallyPaused ? .none : .pause
        case .nominal, .fair:
            return automaticallyPaused ? .resume : .none
        @unknown default:
            return .pause
        }
    }

    static func label(for state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: "Nominal"
        case .fair: "Fair"
        case .serious: "Serious"
        case .critical: "Critical"
        @unknown default: "Unknown"
        }
    }
}

enum DeepResearchResourcePolicy {
    static func maximumWorkerCount(
        activeProcessors: Int,
        reservedProcessors: Int = 2
    ) -> Int {
        max(activeProcessors - max(reservedProcessors, 1), 1)
    }
}

struct DeepResearchControlIdentity: Equatable, Sendable {
    let runID: String
    let nonce: String
    let directory: URL

    var fileURL: URL { directory.appending(path: "\(runID).control.json") }
}

enum DeepResearchControlError: Error, Equatable, LocalizedError, Sendable {
    case malformed
    case identityMismatch
    case terminal

    var errorDescription: String? {
        switch self {
        case .malformed: "The Deep Research control file is unavailable or malformed."
        case .identityMismatch: "The Deep Research control identity does not match the active run."
        case .terminal: "A stopped Deep Research run cannot be resumed."
        }
    }
}

struct DeepResearchControlFile: Sendable {
    private struct Payload: Codable {
        let nonce: String
        let runID: String
        let state: DeepResearchControlState
        let updatedAt: String

        private enum CodingKeys: String, CodingKey {
            case nonce, state
            case runID = "run_id"
            case updatedAt = "updated_at"
        }
    }

    let identity: DeepResearchControlIdentity

    func initialize() throws {
        try write(.running)
    }

    func read() throws -> DeepResearchControlState {
        let payload: Payload
        do {
            payload = try JSONDecoder().decode(Payload.self, from: Data(contentsOf: identity.fileURL))
        } catch {
            throw DeepResearchControlError.malformed
        }
        guard payload.runID == identity.runID, payload.nonce == identity.nonce else {
            throw DeepResearchControlError.identityMismatch
        }
        return payload.state
    }

    func request(_ state: DeepResearchControlState) throws {
        if try read() == .stopped, state != .stopped { throw DeepResearchControlError.terminal }
        try write(state)
    }

    private func write(_ state: DeepResearchControlState) throws {
        try FileManager.default.createDirectory(
            at: identity.directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: identity.directory.path)
        let timestamp = ISO8601DateFormatter().string(from: Date())
        let payload = Payload(nonce: identity.nonce, runID: identity.runID, state: state, updatedAt: timestamp)
        let data = try JSONEncoder().encode(payload)
        try data.write(to: identity.fileURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: identity.fileURL.path)
    }
}
