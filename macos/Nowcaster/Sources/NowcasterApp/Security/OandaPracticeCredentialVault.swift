import Foundation

struct OandaPracticeCredentials: Sendable {
    let accountID: String
    let token: String
}

struct OandaPracticeCredentialVault: Sendable {
    static let service = "com.james8464.nowcaster.oanda.practice"
    private let client: any KeychainClient

    init(client: any KeychainClient = SystemKeychainClient()) { self.client = client }

    var status: String {
        (try? client.load(service: Self.service)) == nil ? "Not connected" : "Practice account stored"
    }

    func load() throws -> OandaPracticeCredentials? {
        guard let item = try client.load(service: Self.service),
              !item.account.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let token = String(data: item.value, encoding: .utf8),
              !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return .init(accountID: item.account, token: token)
    }
}
