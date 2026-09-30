import Foundation
import Testing
@testable import NowcasterApp

@Test func paperSessionLegacyAndPerSourceBindingsRoundTripWithoutChangingIdentity() throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = PaperSessionPreferenceStore(url: root.appending(path: "preferences.json"))
    var preferences = PaperSessionPreferences()
    preferences.source = .init(directory: root.appending(path: "A"), protocolHash: String(repeating: "a", count: 64))
    preferences.campaignID = "original"; preferences.createdAt = "2026-09-30T00:00:00Z"
    preferences.campaignHash = String(repeating: "b", count: 64)
    preferences.runtimeCodeIdentity = String(repeating: "c", count: 64)
    preferences.registryURL = root.appending(path: "shared-registry")
    preferences.manifestURL = root.appending(path: "original.json")
    try store.save(preferences)
    // An old preferences object contains no sourceBindings key.
    var legacy = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: store.url)) as? [String: Any])
    legacy.removeValue(forKey: "sourceBindings")
    try JSONSerialization.data(withJSONObject: legacy).write(to: store.url)
    #expect(store.load().preferences == preferences)
    var selected = store.load().preferences
    selected.select(.init(directory: root.appending(path: "B"), protocolHash: String(repeating: "d", count: 64)))
    selected.campaignID = "separate-B"
    try store.save(selected)
    selected = store.load().preferences
    selected.select(preferences.source!)
    #expect(selected.campaignID == preferences.campaignID)
    #expect(selected.createdAt == preferences.createdAt)
    #expect(selected.runtimeCodeIdentity == preferences.runtimeCodeIdentity)
    #expect(selected.manifestURL == preferences.manifestURL)
    #expect(selected.registryURL == preferences.registryURL)
    #expect(selected.sourceBindings?.count == 2)
}

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
