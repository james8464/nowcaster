import Foundation
import Testing

@testable import NowcasterApp

private final class RecordingKeychainClient: KeychainClient, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: (String, Data)] = [:]
    private(set) var services: [String] = []

    func upsert(service: String, account: String, value: Data) {
        lock.withLock {
            services.append(service)
            values[service] = (account, value)
        }
    }

    func load(service: String) -> (account: String, value: Data)? {
        lock.withLock { values[service].map { (account: $0.0, value: $0.1) } }
    }

    func delete(service: String) {
        _ = lock.withLock { values.removeValue(forKey: service) }
    }
}

@Test func paperAndLiveCredentialsUseDistinctServices() throws {
    let store = RecordingKeychainClient()
    let vault = BrokerCredentialVault(client: store)
    try vault.save(.init(keyID: "paper-1234", secret: "paper-secret"), environment: .paper)
    try vault.save(.init(keyID: "live-5678", secret: "live-secret"), environment: .live)
    #expect(store.services == [
        "com.james8464.nowcaster.alpaca.paper",
        "com.james8464.nowcaster.alpaca.live",
    ])
    #expect(try vault.status(environment: .paper).accountSuffix == "1234")
    #expect(try vault.status(environment: .live).accountSuffix == "5678")
}

@Test func replaceLoadAndDeleteNeverExposeSecretsThroughStatus() throws {
    let store = RecordingKeychainClient()
    let vault = BrokerCredentialVault(client: store)
    try vault.save(.init(keyID: "paper-1234", secret: "first"), environment: .paper)
    try vault.save(.init(keyID: "paper-1234", secret: "replacement"), environment: .paper)
    let stored = try vault.loadForSession(environment: .paper)
    let loaded = try #require(stored)
    #expect(loaded.secret == "replacement")
    #expect(!String(describing: try vault.status(environment: .paper)).contains("replacement"))
    try vault.delete(environment: .paper)
    #expect(try !vault.status(environment: .paper).configured)
}
