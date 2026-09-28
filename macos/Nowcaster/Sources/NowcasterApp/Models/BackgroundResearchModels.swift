import Foundation

enum PaperSessionState: Equatable, Sendable {
    case idle, starting, collecting, researching, waiting, pausing, paused, blocked(String)
}

struct PaperSessionSource: Codable, Equatable, Sendable {
    let directory: URL
    let protocolHash: String
}

struct BackgroundResearchRequest: Equatable, Sendable {
    let registryURL: URL
    let campaignHash: String
    let runID: String
    let controlDirectory: URL
    let controlNonce: String
    var workers: Int = BackgroundResourcePolicy.efficientWorkers(ProcessInfo.processInfo.activeProcessorCount)

    var control: DeepResearchControlFile {
        .init(identity: .init(runID: runID, nonce: controlNonce, directory: controlDirectory))
    }
}

struct BackgroundResearchRegistrationRequest: Equatable, Sendable {
    let registryURL: URL
    let manifestURL: URL
    var expectedCampaignHash: String? = nil
    var expectedRuntimeCodeIdentity: String? = nil
}

struct BackgroundResearchPreparationRequest: Equatable, Sendable {
    let source: PaperSessionSource
    let manifestURL: URL
    let campaignID: String
    let seed: Int
    let createdAt: String
}

enum BackgroundResearchError: Error, LocalizedError {
    case invalidStatus, identityMismatch, missingRegistration, ownershipMismatch, interrupted, invalidPath
    var errorDescription: String? {
        switch self {
        case .invalidStatus: "Research status is incompatible or malformed."
        case .identityMismatch: "The source, campaign or runtime identity changed. Resume is blocked."
        case .missingRegistration: "Select a registered paper desk before enabling learning."
        case .ownershipMismatch: "Research process ownership could not be verified."
        case .interrupted: "Research shutdown reached its deadline. Retained work will be checked before resuming."
        case .invalidPath: "Research storage must be separate from the registered source and protected studies."
        }
    }
}

struct LearningStatus: Codable, Equatable, Sendable {
    enum State: String, Codable, Sendable { case idle, training, waiting, pausing, paused, blocked, completed, failed }
    let schemaVersion: Int
    let campaignHash: String
    let campaignID: String?
    let batchID: String?
    let state: State
    let reason: String
    let attemptCount: Int
    let failureCount: Int
    let batchAttemptCount: Int
    let batchFailureCount: Int
    let lastCheckpoint: String?
    let nextEligibleAt: String?
    let paperOnly: Bool

    enum CodingKeys: String, CodingKey, CaseIterable {
        case schemaVersion = "schema_version", campaignHash = "campaign_hash", campaignID = "campaign_id"
        case batchID = "batch_id", state, reason, attemptCount = "attempt_count", failureCount = "failure_count"
        case batchAttemptCount = "batch_attempt_count", batchFailureCount = "batch_failure_count"
        case lastCheckpoint = "last_checkpoint", nextEligibleAt = "next_eligible_at", paperOnly = "paper_only"
    }

    init(from decoder: Decoder) throws {
        let keys = try decoder.container(keyedBy: AnyCodingKey.self)
        guard Set(keys.allKeys.map(\.stringValue)) == Set(CodingKeys.allCases.map(\.rawValue)) else { throw BackgroundResearchError.invalidStatus }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        campaignHash = try c.decode(String.self, forKey: .campaignHash)
        campaignID = try c.decodeIfPresent(String.self, forKey: .campaignID)
        batchID = try c.decodeIfPresent(String.self, forKey: .batchID)
        state = try c.decode(State.self, forKey: .state)
        reason = try c.decode(String.self, forKey: .reason)
        attemptCount = try c.decode(Int.self, forKey: .attemptCount)
        failureCount = try c.decode(Int.self, forKey: .failureCount)
        batchAttemptCount = try c.decode(Int.self, forKey: .batchAttemptCount)
        batchFailureCount = try c.decode(Int.self, forKey: .batchFailureCount)
        lastCheckpoint = try c.decodeIfPresent(String.self, forKey: .lastCheckpoint)
        nextEligibleAt = try c.decodeIfPresent(String.self, forKey: .nextEligibleAt)
        paperOnly = try c.decode(Bool.self, forKey: .paperOnly)
        guard schemaVersion == 1, paperOnly, Self.isDigest(campaignHash),
              [attemptCount, failureCount, batchAttemptCount, batchFailureCount].allSatisfy({ $0 >= 0 }) else { throw BackgroundResearchError.invalidStatus }
        if let nextEligibleAt {
            guard nextEligibleAt.hasSuffix("Z") || nextEligibleAt.hasSuffix("+00:00") else { throw BackgroundResearchError.invalidStatus }
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            guard formatter.date(from: nextEligibleAt) != nil || ISO8601DateFormatter().date(from: nextEligibleAt) != nil else { throw BackgroundResearchError.invalidStatus }
        }
    }

    static func decode(_ data: Data) throws -> Self {
        try validateJSONTypes(JSONSerialization.jsonObject(with: data))
        return try JSONDecoder().decode(Self.self, from: data)
    }
    static func validateJSONTypes(_ value: Any) throws {
        guard let object = value as? [String: Any] else { throw BackgroundResearchError.invalidStatus }
        for key in ["schema_version", "attempt_count", "failure_count", "batch_attempt_count", "batch_failure_count"] {
            guard let number = object[key] as? NSNumber,
                  ["q", "i", "s", "l", "Q", "I", "S", "L"].contains(String(cString: number.objCType)) else { throw BackgroundResearchError.invalidStatus }
        }
        guard let paperOnly = object["paper_only"] as? NSNumber, String(cString: paperOnly.objCType) == "c", paperOnly.boolValue else { throw BackgroundResearchError.invalidStatus }
    }
    static func isDigest(_ value: String) -> Bool { value.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) } }
}

private struct AnyCodingKey: CodingKey {
    let stringValue: String
    var intValue: Int? { nil }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}

struct BackgroundProcessOwnership: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let runID: String
    let nonce: String
    let pid: Int32
    let parentPID: Int32
    let processGroupID: Int32
    let processStartSeconds: UInt64
    let processStartMicroseconds: UInt64
    let campaignHash: String
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", runID = "run_id", nonce, pid, parentPID = "parent_pid"
        case processGroupID = "process_group_id", processStartSeconds = "process_start_seconds"
        case processStartMicroseconds = "process_start_microseconds", campaignHash = "campaign_hash"
    }
}
