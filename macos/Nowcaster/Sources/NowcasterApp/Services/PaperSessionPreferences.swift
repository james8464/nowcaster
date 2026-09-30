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
    // Optional for backward-compatible decoding of existing retained preferences.
    var sourceBindings: [PaperSessionSourceBinding]?

    mutating func retainCurrentBinding() {
        guard let source else { return }
        var bindings = sourceBindings ?? []
        bindings.removeAll { $0.source == source }
        bindings.append(.init(source: source, registryURL: registryURL, manifestURL: manifestURL,
            campaignID: campaignID, createdAt: createdAt, campaignHash: campaignHash,
            runtimeCodeIdentity: runtimeCodeIdentity, seed: seed))
        sourceBindings = bindings
    }
    mutating func select(_ selected: PaperSessionSource) {
        guard source != selected else { return }
        retainCurrentBinding()
        let retained = sourceBindings?.first { $0.source == selected }
        source = selected
        // A new source shares the registry (peer/exposure accounting), not a campaign.
        registryURL = retained?.registryURL ?? registryURL
        manifestURL = retained?.manifestURL; campaignID = retained?.campaignID
        createdAt = retained?.createdAt; campaignHash = retained?.campaignHash
        runtimeCodeIdentity = retained?.runtimeCodeIdentity; seed = retained?.seed ?? seed
    }
}

struct PaperSessionSourceBinding: Codable, Equatable, Sendable {
    let source: PaperSessionSource
    let registryURL: URL?
    let manifestURL: URL?
    let campaignID: String?
    let createdAt: String?
    let campaignHash: String?
    let runtimeCodeIdentity: String?
    let seed: Int
}

struct PaperSessionPreferenceStore: Sendable {
    let url: URL
    static var application: Self {
        .init(url: AppStorageLocations.root.appending(path: "paper-session.json"))
    }
    func load() -> (preferences: PaperSessionPreferences, explanation: String?) {
        guard FileManager.default.fileExists(atPath: url.path) else { return (.init(), nil) }
        do {
            let preferences = try JSONDecoder().decode(PaperSessionPreferences.self, from: Data(contentsOf: url))
            let bindings = preferences.sourceBindings ?? []
            guard bindings.allSatisfy({ binding in
                binding.seed >= 0 && LearningStatus.isDigest(binding.source.protocolHash)
                    && (binding.campaignHash.map(LearningStatus.isDigest) ?? true)
                    && (binding.runtimeCodeIdentity.map(LearningStatus.isDigest) ?? true)
            }), Set(bindings.map { $0.source.directory.absoluteString + ":" + $0.source.protocolHash }).count == bindings.count else {
                throw BackgroundResearchError.identityMismatch
            }
            if let root = AppStorageLocations.acceptanceRoot {
                let paths = [preferences.source?.directory, preferences.registryURL, preferences.manifestURL]
                    + bindings.flatMap { [Optional($0.source.directory), $0.registryURL, $0.manifestURL] }
                for path in paths.compactMap({ $0 }) {
                    guard path.resolvingSymlinksInPath().path.hasPrefix(root.path + "/") else {
                        throw BackgroundResearchError.invalidPath
                    }
                }
            }
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
