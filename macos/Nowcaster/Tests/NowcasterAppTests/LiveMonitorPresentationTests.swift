import Foundation
import Testing
import UserNotifications

@testable import NowcasterApp

@Test func liveNotificationAuthorizationProjectsOnlyAnAllowedBoolean() {
    #expect(NotificationAuthorizationPolicy.permitsDelivery(.authorized))
    #expect(NotificationAuthorizationPolicy.permitsDelivery(.provisional))
    #expect(!NotificationAuthorizationPolicy.permitsDelivery(.denied))
    #expect(!NotificationAuthorizationPolicy.permitsDelivery(.notDetermined))
}

@Test func liveMonitorColdStartGraceDoesNotRelaxReadyHeartbeatSupervision() {
    let lastEvent = Date(timeIntervalSince1970: 1_000)
    #expect(!LiveMonitorSupervision.shouldRestart(lastEventAt: lastEvent, now: lastEvent.addingTimeInterval(60), hasReceivedReady: false))
    #expect(LiveMonitorSupervision.shouldRestart(lastEventAt: lastEvent, now: lastEvent.addingTimeInterval(181), hasReceivedReady: false))
    #expect(!LiveMonitorSupervision.shouldRestart(lastEventAt: lastEvent, now: lastEvent.addingTimeInterval(45), hasReceivedReady: true))
    #expect(LiveMonitorSupervision.shouldRestart(lastEventAt: lastEvent, now: lastEvent.addingTimeInterval(46), hasReceivedReady: true))
}

@Test func liveMonitorHealthUsesTextAndSymbolsRatherThanColorAlone() {
    #expect(LiveMonitorStatus.healthy.label == "Monitoring")
    #expect(LiveMonitorStatus.healthy.symbol == "checkmark.circle.fill")
    #expect(LiveMonitorStatus.stale.label == "Data Stale")
    #expect(LiveMonitorStatus.stale.symbol != LiveMonitorStatus.healthy.symbol)
}

@Test func decisionPresentationSurfacesCostAdjustedEdgeAndDrift() throws {
    let event = try JSONDecoder().decode(
        LiveMonitorEvent.self,
        from: Data(
            """
            {"schema_version":1,"event_id":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",\
            "sequence":1,"event_type":"decision","emitted_at":"2026-08-28T12:00:00Z",\
            "payload":{"symbol":"BTCUSDT","status":"long","expected_net_edge":"0.0035",\
            "drift_status":"stable","provider":"binance","feed":"spot"}}
            """.utf8
        )
    )
    let summary = LiveMonitorPresentation.summary(for: event)
    #expect(summary.contains("BTCUSDT · long"))
    #expect(summary.contains("+0.35% lower net edge"))
    #expect(summary.contains("drift stable"))
    #expect(summary.contains("binance/spot"))
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
