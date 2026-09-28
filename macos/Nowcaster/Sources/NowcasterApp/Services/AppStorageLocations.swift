import Foundation

/// Explicitly marked acceptance launches use one isolated root for all new paper work.
/// An invalid override aborts launch instead of falling back to user research.
enum AppStorageLocations {
    static func validatedAcceptanceRoot(_ path: String) throws -> URL {
        let original = URL(fileURLWithPath: path).standardizedFileURL
        let root = original.resolvingSymlinksInPath()
        guard path.hasPrefix("/"), root.pathComponents.contains("UIAcceptanceFixtures"),
              !root.pathComponents.contains("ProspectiveStudies"),
              root.lastPathComponent != "UIAcceptanceFixtures",
              FileManager.default.fileExists(atPath: root.appending(path: "UI-TEST-ONLY.md").path)
        else { throw BackgroundResearchError.invalidPath }
        return root
    }
    static let acceptanceRoot: URL? = {
        guard let path = ProcessInfo.processInfo.environment["NOWCASTER_UI_STORAGE_ROOT"] else { return nil }
        do { return try validatedAcceptanceRoot(path) }
        catch { fatalError("Invalid marked UI acceptance storage root; user storage was not opened.") }
    }()
    static var root: URL {
        acceptanceRoot ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appending(path: "Nowcaster")
    }
    static var defaults: UserDefaults {
        guard let acceptanceRoot else { return .standard }
        return UserDefaults(suiteName: "Nowcaster.UIAcceptanceFixtures." + acceptanceRoot.lastPathComponent)!
    }
}
