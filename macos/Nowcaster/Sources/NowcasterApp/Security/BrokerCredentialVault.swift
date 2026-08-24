import Foundation
import Security

enum BrokerCredentialEnvironment: String, CaseIterable, Sendable {
    case paper
    case live

    var service: String { "com.james8464.nowcaster.alpaca.\(rawValue)" }
}

struct BrokerCredentials: Sendable {
    let keyID: String
    let secret: String
}

struct BrokerCredentialStatus: Equatable, Sendable {
    let configured: Bool
    let accountSuffix: String?
}

protocol KeychainClient: Sendable {
    func upsert(service: String, account: String, value: Data) throws
    func load(service: String) throws -> (account: String, value: Data)?
    func delete(service: String) throws
}

enum BrokerCredentialVaultError: Error, Equatable, LocalizedError {
    case keychainStatus(Int32)
    case invalidStoredValue

    var errorDescription: String? {
        switch self {
        case let .keychainStatus(status): "Keychain operation failed (OSStatus \(status))."
        case .invalidStoredValue: "Stored broker credentials are unreadable. Replace them in Settings."
        }
    }
}

struct SystemKeychainClient: KeychainClient {
    func upsert(service: String, account: String, value: Data) throws {
        try delete(service: service)
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecValueData: value,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw BrokerCredentialVaultError.keychainStatus(status) }
    }

    func load(service: String) throws -> (account: String, value: Data)? {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecMatchLimit: kSecMatchLimitOne,
            kSecReturnAttributes: true,
            kSecReturnData: true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess,
              let result = item as? [CFString: Any],
              let account = result[kSecAttrAccount] as? String,
              let data = result[kSecValueData] as? Data
        else { throw BrokerCredentialVaultError.keychainStatus(status) }
        return (account, data)
    }

    func delete(service: String) throws {
        let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw BrokerCredentialVaultError.keychainStatus(status)
        }
    }
}

struct BrokerCredentialVault: Sendable {
    private let client: any KeychainClient

    init(client: any KeychainClient = SystemKeychainClient()) {
        self.client = client
    }

    func save(_ credentials: BrokerCredentials, environment: BrokerCredentialEnvironment) throws {
        guard !credentials.keyID.isEmpty, !credentials.secret.isEmpty else {
            throw BrokerCredentialVaultError.invalidStoredValue
        }
        try client.upsert(
            service: environment.service,
            account: credentials.keyID,
            value: Data(credentials.secret.utf8)
        )
    }

    func status(environment: BrokerCredentialEnvironment) throws -> BrokerCredentialStatus {
        guard let stored = try client.load(service: environment.service) else {
            return BrokerCredentialStatus(configured: false, accountSuffix: nil)
        }
        return BrokerCredentialStatus(configured: true, accountSuffix: String(stored.account.suffix(4)))
    }

    func loadForSession(environment: BrokerCredentialEnvironment) throws -> BrokerCredentials? {
        guard let stored = try client.load(service: environment.service),
              let secret = String(data: stored.value, encoding: .utf8),
              !secret.isEmpty
        else { return nil }
        return BrokerCredentials(keyID: stored.account, secret: secret)
    }

    func delete(environment: BrokerCredentialEnvironment) throws {
        try client.delete(service: environment.service)
    }
}
