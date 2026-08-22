import Foundation
import Testing

@testable import NowcasterApp

@Test @MainActor func settingsPersistOnlyLocalNonsecretPaths() throws {
    let suite = "NowcasterTests-\(UUID().uuidString)"
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let settings = AppSettings(defaults: defaults)
    settings.projectRootPath = "/tmp/project"
    settings.pythonExecutablePath = "/usr/bin/python3"
    settings.snapshotPath = "/tmp/project/data/app/snapshot.json"

    let reloaded = AppSettings(defaults: defaults)
    #expect(reloaded.projectRootPath == "/tmp/project")
    #expect(reloaded.pythonExecutablePath == "/usr/bin/python3")
    #expect(reloaded.snapshotPath.hasSuffix("snapshot.json"))
    #expect(!defaults.dictionaryRepresentation().keys.contains { $0.lowercased().contains("secret") })
}

@Test @MainActor func settingsValidationExplainsMissingPaths() {
    let settings = AppSettings(defaults: UserDefaults.standard)
    settings.projectRootPath = "/definitely/missing/nowcaster"
    settings.pythonExecutablePath = "/definitely/missing/python"
    settings.snapshotPath = "/definitely/missing/snapshot.json"
    let issues = settings.validationIssues()
    #expect(issues.count == 3)
}
