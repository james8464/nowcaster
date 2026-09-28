import Foundation

struct PaperSessionPreferences: Codable, Equatable, Sendable {
    enum ResourceProfile: String, Codable, CaseIterable, Sendable {
        case efficient, balanced
        func workers(cores: Int) -> Int {
            self == .efficient ? BackgroundResourcePolicy.efficientWorkers(cores) : max(cores - 2, 1)
        }
    }
    var learningEnabled = false
    var resumeOnLaunch = false
    var showMenuBarExtra = false
    var resourceProfile: ResourceProfile = .efficient
    var source: PaperSessionSource?
    var registryURL: URL?
    var manifestURL: URL?
    var campaignID: String?
    var createdAt: String?
    var campaignHash: String?
    var runtimeCodeIdentity: String?
    var seed = 42
}

struct PaperSessionPreferenceStore: Sendable {
    let url: URL
    static var application: Self {
        .init(url: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appending(path: "Nowcaster/paper-session.json"))
    }
    func load() -> (preferences: PaperSessionPreferences, explanation: String?) {
        guard FileManager.default.fileExists(atPath: url.path) else { return (.init(), nil) }
        do {
            let preferences = try JSONDecoder().decode(PaperSessionPreferences.self, from: Data(contentsOf: url))
            guard preferences.seed >= 0,
                  preferences.campaignHash.map(LearningStatus.isDigest) ?? true,
                  preferences.runtimeCodeIdentity.map(LearningStatus.isDigest) ?? true,
                  preferences.source.map({ LearningStatus.isDigest($0.protocolHash) }) ?? true else { throw BackgroundResearchError.identityMismatch }
            return (preferences, nil)
        } catch { return (.init(), "Saved paper-session preferences could not be read. Automatic work is disabled.") }
    }
    func save(_ preferences: PaperSessionPreferences) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(preferences).write(to: url, options: .atomic)
    }
}
