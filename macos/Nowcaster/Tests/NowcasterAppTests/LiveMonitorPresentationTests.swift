import Foundation
import Testing

@testable import NowcasterApp

@Test func liveMonitorHealthUsesTextAndSymbolsRatherThanColorAlone() {
    #expect(LiveMonitorStatus.healthy.label == "Monitoring")
    #expect(LiveMonitorStatus.healthy.symbol == "checkmark.circle.fill")
    #expect(LiveMonitorStatus.stale.label == "Data Stale")
    #expect(LiveMonitorStatus.stale.symbol != LiveMonitorStatus.healthy.symbol)
}

@Test func notificationPolicyDeduplicatesAndSuppressesOnlyForegroundOrQuietEntryEvents() {
    var policy = LiveNotificationPolicy()
    let entry = LiveNotificationCandidate(id: "entry-1", category: .entry, title: "AAPL Long Setup", body: "Research alert")
    let stop = LiveNotificationCandidate(id: "stop-1", category: .stop, title: "AAPL Stop", body: "Setup stopped")

    let admitted = policy.admit(entry, appIsActive: false, quietHours: false)
    let duplicate = policy.admit(entry, appIsActive: false, quietHours: false)
    let foreground = policy.admit(LiveNotificationCandidate(id: "entry-2", category: .entry, title: "Entry", body: "Body"), appIsActive: true, quietHours: false)
    let quietEntry = policy.admit(LiveNotificationCandidate(id: "entry-3", category: .entry, title: "Entry", body: "Body"), appIsActive: false, quietHours: true)
    let quietStop = policy.admit(stop, appIsActive: false, quietHours: true)
    #expect(admitted)
    #expect(!duplicate)
    #expect(!foreground)
    #expect(!quietEntry)
    #expect(quietStop)
}

@Test @MainActor func monitorSettingsPersistWatchlistsWithoutSecrets() throws {
    let suite = "NowcasterMonitorTests-\(UUID().uuidString)"
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let settings = AppSettings(defaults: defaults)
    settings.stockWatchlist = "aapl, SPY, AAPL"
    settings.cryptoWatchlist = "btcusdt, ethusdt"
    settings.monitorAtLogin = true

    let restored = AppSettings(defaults: defaults)
    #expect(restored.normalizedStocks == ["AAPL", "SPY"])
    #expect(restored.normalizedCrypto == ["BTCUSDT", "ETHUSDT"])
    #expect(restored.monitorAtLogin)
    #expect(!defaults.dictionaryRepresentation().keys.contains { $0.lowercased().contains("secret") })
}
