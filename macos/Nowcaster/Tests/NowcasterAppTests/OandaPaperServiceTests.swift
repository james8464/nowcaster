import Foundation
import Testing

@testable import NowcasterApp

private struct OandaTestKeychain: KeychainClient {
    let account: String?
    let token: String?
    func upsert(service: String, account: String, value: Data) throws {}
    func load(service: String) throws -> (account: String, value: Data)? {
        guard service == OandaPracticeCredentialVault.service, let account, let token else { return nil }
        return (account, Data(token.utf8))
    }
    func delete(service: String) throws {}
}

@Test func oandaVaultUsesTheExistingPracticeKeychainItem() throws {
    let vault = OandaPracticeCredentialVault(client: OandaTestKeychain(account: "practice-id", token: "secret-token"))
    let loaded = try vault.load()
    let credentials = try #require(loaded)
    #expect(credentials.accountID == "practice-id")
    #expect(credentials.token == "secret-token")
    #expect(!String(describing: vault.status).contains("secret-token"))
}

@Test func oandaRunnerArgumentsNeverIncludeTheCredential() throws {
    let config = OandaPaperServiceConfiguration(
        executable: URL(fileURLWithPath: "/tmp/nowcaster-oanda-paper"),
        script: nil, directory: URL(fileURLWithPath: "/tmp/OandaPaper")
    )
    let arguments = config.arguments
    #expect(arguments == ["run", "--directory", "/tmp/OandaPaper"])
    #expect(!arguments.joined().contains("secret-token"))
}

@Test func diagnosticSetupCannotBecomeAPaperNotification() throws {
    let text = """
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-08T09:00:00Z","feed_health":"healthy",
     "evidence_status":"not_supported","markets":[{"market":"germany40","broker_symbol":"DE30_EUR",
     "product":"cfd","eligibility":"diagnostic","reason":"costs unverified"}],
     "opportunities":[{"market":"germany40","broker_symbol":"DE30_EUR","strategy_id":"trend_pullback",
     "direction":"long","decided_at":"2026-10-08T08:59:59Z","entry_at":"2026-10-08T09:00:00Z",
     "entry":"101","stop":"96","target":"111","exit_by":"2026-10-08T10:00:00Z",
     "estimated_roundtrip_cost":"1","explanation":"diagnostic only","evidence_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","paper_only":true}],
     "paper_positions":[],"no_trade_reason":""}
    """
    let status = try IntradayDeskStatus.decode(Data(text.utf8))
    let now = ISO8601DateFormatter().date(from: "2026-10-08T09:00:10Z")!
    #expect(OandaPaperNotificationGate.eligibleSetupIDs(status, at: now).isEmpty)
}

@Test func paperCloseRequiresFreshEligibleMarket() throws {
    let template = """
    {"schema_version":1,"paper_only":true,"generated_at":"2026-10-08T09:00:00Z","feed_health":"%@",
     "evidence_status":"not_supported","markets":[{"market":"germany40","broker_symbol":"DE30_EUR",
     "product":"cfd","eligibility":"%@"}],"opportunities":[],"paper_positions":[],"no_trade_reason":"No trade"}
    """
    let now = ISO8601DateFormatter().date(from: "2026-10-08T09:00:10Z")!
    let healthy = try IntradayDeskStatus.decode(Data(String(format: template, "healthy", "paper_eligible").utf8))
    let diagnostic = try IntradayDeskStatus.decode(Data(String(format: template, "healthy", "diagnostic").utf8))
    let stale = try IntradayDeskStatus.decode(Data(String(format: template, "stale", "paper_eligible").utf8))
    #expect(OandaPaperNotificationGate.canNotifyClose(healthy, brokerSymbol: "DE30_EUR", at: now))
    #expect(!OandaPaperNotificationGate.canNotifyClose(diagnostic, brokerSymbol: "DE30_EUR", at: now))
    #expect(!OandaPaperNotificationGate.canNotifyClose(stale, brokerSymbol: "DE30_EUR", at: now))
}

@Test func staleExperimentalStatusCannotNotify() throws {
    let text = """
    {"schema_version":1,"paper_only":true,"qualification":"unqualified","round_id":"round-a",
     "cohort_id":"cohort-a","generated_at":"2026-10-20T07:20:03Z","broker_symbol":"DE30_EUR",
     "feed_health":"healthy","quote_age_seconds":"0","contiguous_bars":4,"gap_count":0,
     "no_trade_reason":"paper_position_open","paper_ticket":null}
    """
    let status = try ExperimentalPaperStatus.decode(Data(text.utf8))
    let later = ISO8601DateFormatter().date(from: "2026-10-20T07:25:03Z")!
    #expect(!OandaPaperNotificationGate.canNotifyExperimental(status, at: later))
}

@Test @MainActor func pauseWithOpenPositionDoesNotForceClose() async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let shell = root.appending(path: "python")
    try FileManager.default.createSymbolicLink(at: shell, withDestinationURL: URL(fileURLWithPath: "/bin/sh"))
    let script = root.appending(path: "intraday_service_entry.py")
    try Data("sleep 2\n".utf8).write(to: script)
    let directory = root.appending(path: "research")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    try Data("""
    {"schema_version":1,"paper_only":true,"qualification":"unqualified","round_id":"round-a",
     "cohort_id":"cohort-a","generated_at":"2026-10-20T07:20:03Z","broker_symbol":"DE30_EUR",
     "feed_health":"healthy","quote_age_seconds":"0","contiguous_bars":4,"gap_count":0,
     "no_trade_reason":"paper_position_open","paper_ticket":{"qualification":"unqualified"}}
    """.utf8).write(to: directory.appending(path: "experimental-summary.json"))
    let vault = OandaPracticeCredentialVault(client: OandaTestKeychain(account: "practice-id", token: "test-only-token"))
    let service = OandaPaperService(vault: vault, configuration: .init(executable: shell, script: script, directory: directory))
    service.start()
    #expect(service.isRunning)
    service.shutdown()
    try await Task.sleep(for: .milliseconds(200))
    #expect(service.isRunning)
    #expect(FileManager.default.fileExists(atPath: directory.appending(path: "shutdown.request").path))
    try await Task.sleep(for: .seconds(2))
}

@Test @MainActor func shutdownWaitReportsUnacknowledgedHelper() async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let shell = root.appending(path: "python")
    try FileManager.default.createSymbolicLink(at: shell, withDestinationURL: URL(fileURLWithPath: "/bin/sh"))
    let script = root.appending(path: "intraday_service_entry.py")
    try Data("while test ! -f \"$3/release.flag\"; do sleep 0.1; done\n".utf8).write(to: script)
    let directory = root.appending(path: "research")
    let vault = OandaPracticeCredentialVault(client: OandaTestKeychain(account: "practice-id", token: "test-only-token"))
    let service = OandaPaperService(vault: vault, configuration: .init(executable: shell, script: script, directory: directory))
    service.start()
    #expect(service.isRunning)
    let acknowledged = await service.shutdownAndWait(timeout: .milliseconds(100))
    #expect(!acknowledged)
    #expect(service.message?.contains("still running") == true)
    try Data().write(to: directory.appending(path: "release.flag"))
    try await Task.sleep(for: .milliseconds(200))
}

@Test @MainActor func shutdownWaitAcceptsHelperExitAcknowledgment() async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let shell = root.appending(path: "python")
    try FileManager.default.createSymbolicLink(at: shell, withDestinationURL: URL(fileURLWithPath: "/bin/sh"))
    let script = root.appending(path: "intraday_service_entry.py")
    try Data("while test ! -f \"$3/shutdown.request\"; do sleep 0.1; done\n".utf8).write(to: script)
    let directory = root.appending(path: "research")
    let vault = OandaPracticeCredentialVault(client: OandaTestKeychain(account: "practice-id", token: "test-only-token"))
    let service = OandaPaperService(vault: vault, configuration: .init(executable: shell, script: script, directory: directory))
    service.start()
    #expect(service.isRunning)
    #expect(await service.shutdownAndWait(timeout: .seconds(3)))
    #expect(FileManager.default.fileExists(atPath: directory.appending(path: "shutdown.request").path))
}
