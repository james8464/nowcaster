import Foundation
import Testing
@testable import NowcasterApp

@Test func paperSessionCorruptPreferencesDisableAutomaticWorkWithExplanation() throws {
    let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = PaperSessionPreferenceStore(url: directory.appending(path: "preferences.json"))
    try Data("{broken".utf8).write(to: store.url)
    let loaded = store.load()
    #expect(!loaded.preferences.learningEnabled); #expect(!loaded.preferences.resumeOnLaunch)
    #expect(loaded.explanation != nil)
    var changed = loaded.preferences; changed.showMenuBarExtra = true
    try store.save(changed)
    #expect(store.load().preferences == changed)
}
