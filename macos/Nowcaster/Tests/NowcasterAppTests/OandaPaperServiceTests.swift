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
