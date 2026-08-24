import SwiftUI

struct BrokerCredentialsView: View {
    let vault: BrokerCredentialVault
    @State private var environment: BrokerCredentialEnvironment = .paper
    @State private var keyID = ""
    @State private var secret = ""
    @State private var status = BrokerCredentialStatus(configured: false, accountSuffix: nil)
    @State private var message: String?

    var body: some View {
        Section("Broker credentials") {
            Picker("Environment", selection: $environment) {
                ForEach(BrokerCredentialEnvironment.allCases, id: \.self) { value in
                    Text(value.rawValue.capitalized).tag(value)
                }
            }
            SecureField("API key ID", text: $keyID)
            SecureField("API secret", text: $secret)
            HStack {
                Button(status.configured ? "Replace" : "Save") { save() }
                    .disabled(keyID.isEmpty || secret.isEmpty)
                Button("Delete", role: .destructive) { delete() }.disabled(!status.configured)
                Spacer()
                Text(status.configured ? "Configured ••••\(status.accountSuffix ?? "")" : "Not configured")
                    .foregroundStyle(.secondary)
            }
            if let message { Text(message).font(.caption).foregroundStyle(.secondary) }
            Text("Credentials stay in this Mac’s Keychain and are never written to the snapshot or command arguments.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .onAppear { refresh() }
        .onChange(of: environment) { _, _ in clearAndRefresh() }
    }

    private func save() {
        do {
            try vault.save(BrokerCredentials(keyID: keyID, secret: secret), environment: environment)
            message = "Saved to Keychain."
            clearSecrets()
            refresh()
        } catch { message = error.localizedDescription }
    }

    private func delete() {
        do {
            try vault.delete(environment: environment)
            message = "Deleted from Keychain."
            clearSecrets()
            refresh()
        } catch { message = error.localizedDescription }
    }

    private func clearAndRefresh() {
        clearSecrets()
        message = nil
        refresh()
    }

    private func clearSecrets() {
        keyID = ""
        secret = ""
    }

    private func refresh() {
        status = (try? vault.status(environment: environment)) ?? .init(configured: false, accountSuffix: nil)
    }
}
