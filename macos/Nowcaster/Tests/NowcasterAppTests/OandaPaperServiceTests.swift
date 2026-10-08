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
