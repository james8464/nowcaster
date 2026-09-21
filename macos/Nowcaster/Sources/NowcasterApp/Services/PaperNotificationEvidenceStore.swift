import Foundation

struct PaperNotificationLocation: Codable, Equatable, Sendable {
    let materialKey: String
    let candidateHash: String
    let protocolHash: String
    let directoryPath: String

    var directory: URL { URL(fileURLWithPath: directoryPath) }

    func validate() throws {
        guard [materialKey, candidateHash, protocolHash].allSatisfy({ value in
            value.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) }
        }), directoryPath.hasPrefix("/"), directoryPath.utf8.count <= 4096,
        !directory.resolvingSymlinksInPath().path.contains("/ProspectiveStudies/") else {
            throw LivePaperServiceError.invalidConfiguration
        }
    }
}

/// This index is written only from a validated local reservation, before macOS
/// scheduling. Notification metadata is never allowed to supply a file path.
struct PaperNotificationEvidenceStore: Sendable {
    let directory: URL

    init(directory: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appending(path: "Nowcaster/PaperNotificationIndex")) {
        self.directory = directory
    }

    func record(_ notice: LivePaperNotification, directory researchDirectory: URL) throws {
        let location = PaperNotificationLocation(materialKey: notice.materialKey, candidateHash: notice.candidateHash,
            protocolHash: notice.protocolHash, directoryPath: researchDirectory.resolvingSymlinksInPath().path)
        try location.validate()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let url = directory.appending(path: notice.materialKey + ".json")
        if FileManager.default.fileExists(atPath: url.path) {
            guard try lookup(notice.materialKey) == location else { throw LivePaperServiceError.invalidConfiguration }
            return
        }
        let data = try JSONEncoder().encode(location)
        do { try data.write(to: url, options: .withoutOverwriting) }
        catch {
            guard try lookup(notice.materialKey) == location else { throw error }
            return
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        let file = try FileHandle(forWritingTo: url)
        defer { try? file.close() }
        try file.synchronize()
    }

    func lookup(_ materialKey: String) throws -> PaperNotificationLocation {
        guard materialKey.count == 64, materialKey.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw LivePaperServiceError.invalidConfiguration
        }
        let file = try FileHandle(forReadingFrom: directory.appending(path: materialKey + ".json"))
        defer { try? file.close() }
        let data = try file.read(upToCount: 16_385) ?? Data()
        guard data.count <= 16_384,
              let fields = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(fields.keys) == ["materialKey", "candidateHash", "protocolHash", "directoryPath"] else {
            throw LivePaperServiceError.invalidConfiguration
        }
        let location = try JSONDecoder().decode(PaperNotificationLocation.self, from: data)
        try location.validate()
        guard location.materialKey == materialKey else { throw LivePaperServiceError.invalidConfiguration }
        return location
    }
}

struct LivePaperNotificationEvidence: Sendable {
    let notification: LivePaperNotification
    let suggestion: TrendAdvisorSuggestion
    let outcome: String

    static func decode(_ data: Data, location: PaperNotificationLocation) throws -> Self {
        guard data.count <= 65_536, let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(root.keys) == ["notification", "suggestion", "outcome"],
              let rawNotice = root["notification"] as? [String: Any],
              let rawSuggestion = root["suggestion"] as? [String: Any],
              let outcome = root["outcome"] as? String, ["pending", "delivered", "failed"].contains(outcome) else {
            throw LivePaperServiceError.invalidConfiguration
        }
        let notice = try LivePaperNotification.decodeReservation(JSONSerialization.data(withJSONObject: rawNotice),
                                                                 protocolHash: location.protocolHash)
        let suggestion = try JSONDecoder.nowcaster.decode(TrendAdvisorSuggestion.self,
            from: JSONSerialization.data(withJSONObject: rawSuggestion))
        guard notice.materialKey == location.materialKey, notice.candidateHash == location.candidateHash,
              suggestion.candidateHash == notice.candidateHash, suggestion.protocolHash == notice.protocolHash,
              suggestion.symbol == notice.symbol, suggestion.expiresAt == notice.expiresAt,
              suggestion.posture == "long_research", suggestion.decisionAt <= notice.generatedAt else {
            throw LivePaperServiceError.invalidConfiguration
        }
        return Self(notification: notice, suggestion: suggestion, outcome: outcome)
    }
}
