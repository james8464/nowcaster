import Foundation
import Testing
@testable import NowcasterApp

@Suite struct LivePaperSignalsPresentationTests {
    @Test func disconnectedAndExpiredStateNeverLooksLive() {
        let now = Date()
        let state = LivePaperSignalState(kind: "warming", protocolHash: String(repeating: "a", count: 64),
            updatedAt: now, evaluatedAt: now, reasons: ["continuity_warmup"], suggestion: nil)
        #expect(LivePaperSignalsPresentation(state: state, isRunning: true, now: now).status == "Warming up")
        #expect(LivePaperSignalsPresentation(state: state, isRunning: false, now: now).status == "Stopped")
        #expect(LivePaperSignalsPresentation(state: state, isRunning: true, now: now.addingTimeInterval(16)).status == "Stale")
        #expect(LivePaperSignalsPresentation(state: state, isRunning: true, now: now.addingTimeInterval(-1)).status == "Unavailable")
    }

    @Test func bundledHelperDoesNotDependOnSourceCheckout() throws {
        let bundle = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + ".app")
        defer { try? FileManager.default.removeItem(at: bundle) }
        let helper = bundle.appending(path: "Contents/Helpers/nowcaster-paper-signals.app/Contents/MacOS/nowcaster-paper-signals")
        try FileManager.default.createDirectory(at: helper.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: helper)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: helper.path)
        let configuration = try LivePaperSignalConfiguration.application(directory: bundle,
            protocolHash: String(repeating: "a", count: 64), bundleURL: bundle,
            sourceRoot: URL(fileURLWithPath: "/nonexistent"), sourcePython: URL(fileURLWithPath: "/missing"))
        #expect(configuration.executable == helper)
        #expect(configuration.script == nil)
        #expect(configuration.projectRoot == bundle)
        #expect(configuration.arguments(for: "status") == ["status", "--directory", bundle.path])
    }

    @Test @MainActor func paperNotificationOpensOnlyResearchEvidence() {
        let model = AppModel()
        model.destination = .today
        model.openPaperResearchNotification(destination: "execution", materialKey: String(repeating: "a", count: 64))
        #expect(model.destination == .today)
        model.openPaperResearchNotification(destination: "strategy_lab_evidence", materialKey: "bad")
        #expect(model.destination == .today)
        model.openPaperResearchNotification(destination: "strategy_lab_evidence", materialKey: String(repeating: "a", count: 64))
        #expect(model.destination == .strategyLab)
        #expect(model.paperResearchEvidenceRequested)
        #expect(!model.livePaperSignals.isRunning)
        #expect(!model.livePaperSignals.notificationsEnabled)
    }

    @Test func setupDoesNotWeakenRegisteredSessionValidation() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let script = root.appending(path: "run_live_paper_signals.py")
        try Data().write(to: script)
        let config = LivePaperSignalConfiguration(projectRoot: root, executable: URL(fileURLWithPath: "/usr/bin/true"),
            script: script, directory: root.appending(path: "new-desk"), protocolHash: String(repeating: "0", count: 64))
        try config.validate(requireRegistered: false)
        #expect(throws: LivePaperServiceError.self) { try config.validate() }
        let protected = root.appending(path: "ProspectiveStudies/retained")
        try FileManager.default.createDirectory(at: protected, withIntermediateDirectories: true)
        let alias = root.appending(path: "alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: protected)
        let bad = LivePaperSignalConfiguration(projectRoot: root, executable: config.executable,
            script: script, directory: alias, protocolHash: config.protocolHash)
        #expect(throws: LivePaperServiceError.self) { try bad.validate(requireRegistered: false) }
    }

    @Test @MainActor func initialDeskDoesNotStartCollectionOrNotifications() {
        let model = AppModel()
        #expect(model.destination == .tradeDesk)
        #expect(!model.livePaperSignals.isRunning)
        #expect(!model.livePaperSignals.notificationsEnabled)
    }

    @Test @MainActor func notificationClickBeforeWindowCreationIsRetained() {
        let delegate = NowcasterApplicationDelegate()
        delegate.receivePaperResearchNotification(destination: "strategy_lab_evidence", materialKey: String(repeating: "b", count: 64))
        let model = AppModel()
        model.destination = .today
        delegate.model = model
        #expect(model.destination == .strategyLab)
        #expect(model.paperResearchEvidenceRequested)
        #expect(!model.livePaperSignals.isRunning)
    }
}
