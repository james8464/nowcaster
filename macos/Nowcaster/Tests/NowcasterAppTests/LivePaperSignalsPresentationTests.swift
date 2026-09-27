import Foundation
import Testing
@testable import NowcasterApp

@Suite struct LivePaperSignalsPresentationTests {
    @Test func providerHealthDoesNotRequireStrategyEvaluation() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let identity = String(repeating: "a", count: 64)
        let payload: [String: Any] = ["protocolHash": identity, "providerHealth": [
            "provider": "binance", "feed": "spot", "revision": "binance-spot-public-v1",
            "reportedAt": "2026-09-21T12:00:02Z", "lastSuccessfulObservationAt": "2026-09-21T12:00:00Z",
            "maximumAgeSeconds": 15, "state": "degraded", "exclusions": ["continuity_warmup"]]]
        let file = directory.appending(path: "live-paper-provider-health.json")
        try JSONSerialization.data(withJSONObject: payload).write(to: file)
        let health = try #require(try LivePaperSignalService.readProviderHealth(directory, protocolHash: identity))
        #expect(health.lastSuccessfulObservationAt != nil)
        #expect(health.state == "degraded")
        #expect(health.title(now: health.reportedAt.addingTimeInterval(16)) == "Stale")
        #expect(throws: LivePaperServiceError.self) {
            try LivePaperSignalService.readProviderHealth(directory, protocolHash: String(repeating: "b", count: 64))
        }
        try Data("{}".utf8).write(to: file)
        #expect(throws: LivePaperServiceError.self) { try LivePaperSignalService.readProviderHealth(directory, protocolHash: identity) }
    }

    @Test func optInRetainedSyntheticReplayDecodesWithoutModification() throws {
        guard let path = ProcessInfo.processInfo.environment["NOWCASTER_UI_REPLAY_DIRECTORY"] else { return }
        let directory = URL(fileURLWithPath: path)
        #expect(FileManager.default.fileExists(atPath: directory.appending(path: "UI-TEST-ONLY.md").path))
        let report = try JSONDecoder.nowcaster.decode(ResearchRoundSnapshot.self,
            from: Data(contentsOf: directory.appending(path: "research-round-2-summary.json")))
        _ = try LivePaperSignalEvent.decodeHistory(Data(contentsOf: directory.appending(path: "signal-events.jsonl")), now: Date())
        _ = try LivePaperSignalState.decode(Data(contentsOf: directory.appending(path: "live-paper-signal-state.json")), protocolHash: report.protocolHash, now: Date())
    }
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
